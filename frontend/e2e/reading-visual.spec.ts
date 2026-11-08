import { expect, test, type Locator, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  defaultBatches, MCP_OUTPUT_TAIL, ORDINARY_OUTPUT_TAIL, RUNNING_OUTPUT_TAIL,
  startConversationServer, type ConversationServer,
} from './fixtures/conversation-server';
import { openConversation, selectTheme } from './fixtures/reading-page';

// Spec143BE 视觉验收：同一 fixture、视口、展开状态和滚动锚点，通过真实主题菜单切换。
const dir = process.env.CAPTURE_DIR ?? '';
test.skip(!dir, '设置 CAPTURE_DIR=<目录> 后运行视觉截图');
if (dir) mkdirSync(dir, { recursive: true });

const captures: Record<string, unknown>[] = [];

function toolCard(page: Page, target: string) {
  return page.locator('details', { has: page.locator('summary', { hasText: target }) }).first();
}

async function positionSummary(page: Page, card: Locator) {
  const summary = card.locator('summary');
  await summary.evaluate(element => {
    const scroller = element.closest('[class*="conversation"]') as HTMLElement | null;
    if (!scroller) throw new Error('conversation_scroller_missing');
    const delta = element.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    scroller.scrollTop += delta - 96;
  });
  await expect.poll(() => summary.evaluate(element => {
    const scroller = element.closest('[class*="conversation"]') as HTMLElement;
    return Math.round(element.getBoundingClientRect().top - scroller.getBoundingClientRect().top);
  })).toBe(96);
  await page.evaluate(() => document.fonts.ready);
  return page.locator('[class*="conversation"]').first().evaluate(element => element.scrollTop);
}

async function capture(page: Page, name: string, metadata: Record<string, unknown>) {
  const shot = await page.screenshot({ path: join(dir, `${name}.png`), fullPage: false, animations: 'disabled' });
  captures.push({ name, bytes: shot.byteLength, ...metadata });
}

async function drainStream(server: ConversationServer, page: Page) {
  const batches = defaultBatches();
  for (let index = 1; index < batches.length; index++) {
    expect(server.advanceStream()).toBe(true);
    await expect(page.getByText(`阅读连续性检查 ${index + 1}`, { exact: false })).toBeVisible();
  }
  server.finishStream();
  await expect(page.locator('[data-message-id="assistant-live"]')).toContainText('阅读连续性检查 8');
}

async function prepareStatic(page: Page, theme: 'light' | 'dark', expanded: 'ordinary' | 'mcp') {
  const server = startConversationServer();
  await server.listen();
  try {
    await openConversation(page, { port: server.port });
    await selectTheme(page, theme);
    await page.getByRole('button', { name: '加载更早消息' }).click();
    await expect(page.locator('[data-message-id="m-089"]')).toBeVisible();
    const target = expanded === 'ordinary' ? 'seq 1 2000' : 'mcp_search';
    const card = toolCard(page, target);
    await card.locator('summary').click();
    await expect(card).toHaveAttribute('open', '');
    const result = card.locator('pre').last();
    await expect(result).toContainText(expanded === 'ordinary' ? ORDINARY_OUTPUT_TAIL : MCP_OUTPUT_TAIL);
    const scrollTop = await positionSummary(page, card);
    await expect(result).toBeInViewport({ ratio: 0.1 });
    return { server, card, result, scrollTop };
  } catch (error) {
    await server.close();
    throw error;
  }
}

const shots: { width: number; height: number; theme: 'light' | 'dark'; name: string }[] = [
  { width: 1440, height: 900, theme: 'light', name: 'desktop-light' },
  { width: 1440, height: 900, theme: 'dark', name: 'desktop-dark' },
  { width: 375, height: 812, theme: 'light', name: 'mobile-light' },
  { width: 375, height: 812, theme: 'dark', name: 'mobile-dark' },
];

for (const shot of shots) {
  test(`完成态普通长输出：${shot.name}`, async ({ page }) => {
    await page.setViewportSize({ width: shot.width, height: shot.height });
    const { server, card, result, scrollTop } = await prepareStatic(page, shot.theme, 'ordinary');
    try {
      await capture(page, shot.name, { scenario: 'ordinary-result', theme: shot.theme,
        viewport: { width: shot.width, height: shot.height }, fixture: 'reading-v2', historyLoadedThrough: 'm-089',
        expanded: 't1', scrollAnchor: { summaryTop: 96, scrollTop }, resultMaxHeight: await result.evaluate(el => getComputedStyle(el).maxHeight) });
      await expect(card).toHaveAttribute('open', '');
    } finally {
      await server.close();
    }
  });
}

for (const theme of ['light', 'dark'] as const) {
  test(`完成态 MCP 长输出：${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const { server, card, result, scrollTop } = await prepareStatic(page, theme, 'mcp');
    try {
      await capture(page, `desktop-${theme}-mcp`, { scenario: 'mcp-result', theme,
        viewport: { width: 1440, height: 900 }, fixture: 'reading-v2', historyLoadedThrough: 'm-089',
        expanded: 't2', scrollAnchor: { summaryTop: 96, scrollTop }, resultMaxHeight: await result.evaluate(el => getComputedStyle(el).maxHeight) });
      await expect(card).toHaveAttribute('open', '');
    } finally {
      await server.close();
    }
  });

  test(`运行中阶段耗时与输出尾部：${theme}`, async ({ page }) => {
    const viewport = theme === 'light' ? { width: 1440, height: 900 } : { width: 375, height: 812 };
    await page.setViewportSize(viewport);
    const server = startConversationServer({ live: true, runningTool: true });
    await server.listen();
    try {
      await openConversation(page, { port: server.port });
      await selectTheme(page, theme);
      const card = toolCard(page, 'npm run reading:long-output');
      const summary = card.locator('summary');
      await expect(summary).toContainText('输出更新');
      await expect(summary).toContainText('已历时');
      await summary.click();
      await expect(card).toHaveAttribute('open', '');
      await expect(card.locator('[class*="toolDetail"]')).toContainText(RUNNING_OUTPUT_TAIL);
      const scrollTop = await positionSummary(page, card);
      await expect(card.locator('pre').first()).toBeInViewport({ ratio: 0.1 });
      await capture(page, `${theme === 'light' ? 'desktop' : 'mobile'}-${theme}-running`, {
        scenario: 'running-tool', theme, viewport, fixture: 'reading-v2', expanded: 'live-tool',
        phase: 'process_output', scrollAnchor: { summaryTop: 96, scrollTop },
        summary: await summary.textContent(),
      });
      await drainStream(server, page);
    } finally {
      await server.close();
    }
  });
}

test.afterAll(() => {
  if (dir) writeFileSync(join(dir, 'capture-manifest.json'), JSON.stringify({ captures }, null, 2));
});
