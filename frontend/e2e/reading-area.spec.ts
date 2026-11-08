import { expect, test, type Locator, type Page } from '@playwright/test';
import {
  defaultBatches, MCP_OUTPUT_TAIL, ORDINARY_OUTPUT_TAIL, RUN_ID, SCOPE, SESSION_ID,
  startConversationServer,
} from './fixtures/conversation-server';
import { openConversation } from './fixtures/reading-page';

// Spec143BE：主对话阅读区行为回归。测试专用会话服务提供真实 HTTP/SSE 分批数据。
const BATCH1_HEAD = '这是流式回答的第 1 批内容。';

// 阅读区交互从桌面布局开始；工具卡用例会在同一会话内调整到 375px 复验。
test.skip(({ viewport }) => (viewport?.width ?? 0) <= 760, '桌面入口；窄屏在用例内显式调整');

function toolCard(page: Page, target: string) {
  return page.locator('details', { has: page.locator('summary', { hasText: target }) }).first();
}

async function expectTailReachable(pre: Locator, tail: string) {
  await expect(pre).toContainText(tail);
  const result = await pre.evaluate(element => {
    element.scrollTop = element.scrollHeight;
    const range = document.createRange();
    range.selectNodeContents(element);
    const rects = range.getClientRects();
    const last = rects.item(rects.length - 1);
    const bounds = element.getBoundingClientRect();
    return {
      atBottom: Math.abs(element.scrollHeight - element.clientHeight - element.scrollTop) <= 2,
      tailInside: !!last && last.bottom <= bounds.bottom + 1 && last.bottom >= bounds.top,
    };
  });
  expect(result, '结果尾部应通过真实滚动到达可视区域').toEqual({ atBottom: true, tailInside: true });
}

async function exerciseLongResult(card: Locator, summary: Locator, tail: string) {
  await summary.click();
  await expect(card).toHaveAttribute('open', '');
  const pre = card.locator('pre').last();
  await expect(pre).toBeVisible();
  const style = await pre.evaluate(element => {
    const computed = getComputedStyle(element);
    return { maxHeight: computed.maxHeight, overflowY: computed.overflowY,
      scroll: element.scrollHeight, client: element.clientHeight };
  });
  expect(style.maxHeight).toBe('320px');
  expect(style.overflowY).toBe('auto');
  expect(style.scroll).toBeGreaterThan(style.client);
  expect(style.client).toBeLessThanOrEqual(320);
  await pre.hover();
  await pre.page().mouse.wheel(0, 2400);
  await expect.poll(() => pre.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
  await expectTailReachable(pre, tail);
  return pre;
}

test('阅读区：会话打开后渲染近期消息、历史入口与工具卡', async ({ page }) => {
  const server = startConversationServer();
  await server.listen();
  try {
    await openConversation(page, { port: server.port });
    await expect(page.getByRole('button', { name: '加载更早消息' })).toBeVisible();
    await expect(page.getByText('历史回答 119', { exact: false }).first()).toBeVisible();
    await expect(page.locator('summary', { hasText: 'bash' }).first()).toBeVisible();
    await expect(page.locator('summary', { hasText: 'mcp_search' })).toBeVisible();
    expect(server.calls.some(call => call.includes('UNMOCKED'))).toBe(false);
  } finally {
    await server.close();
  }
});

test('长历史：向上加载后新增确定消息，近期消息保持可见', async ({ page }) => {
  const server = startConversationServer();
  await server.listen();
  try {
    await openConversation(page, { port: server.port });
    const before = await page.locator('[data-message-id]').count();
    await page.getByRole('button', { name: '加载更早消息' }).click();
    await expect(page.locator('[data-message-id="m-089"]')).toBeVisible();
    await expect(page.getByText('历史回答 119', { exact: false }).first()).toBeVisible();
    const after = await page.locator('[data-message-id]').count();
    expect(after).toBeGreaterThan(before);
  } finally {
    await server.close();
  }
});

test('工具结果：普通与 MCP 长输出均限高、真实滚动且窄屏展开可达', async ({ page }) => {
  const server = startConversationServer();
  await server.listen();
  try {
    await openConversation(page, { port: server.port });
    const ordinary = toolCard(page, 'seq 1 2000');
    const mcp = toolCard(page, 'mcp_search');
    const ordinaryPre = await exerciseLongResult(ordinary, ordinary.locator('summary'), ORDINARY_OUTPUT_TAIL);
    const mcpPre = await exerciseLongResult(mcp, mcp.locator('summary'), MCP_OUTPUT_TAIL);

    await page.setViewportSize({ width: 375, height: 812 });
    for (const [card, pre, tail] of [[ordinary, ordinaryPre, ORDINARY_OUTPUT_TAIL], [mcp, mcpPre, MCP_OUTPUT_TAIL]] as const) {
      await expect(card).toHaveAttribute('open', '');
      await expect(card).toBeVisible();
      await expect(pre).toBeVisible();
      await expectTailReachable(pre, tail);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  } finally {
    await server.close();
  }
});

test('工具卡摘要：基线、40px 最小高度及运行/完成阶段耗时缩进一致', async ({ page }) => {
  const server = startConversationServer({ live: true, runningTool: true });
  await server.listen();
  try {
    await openConversation(page, { port: server.port });
    const card = toolCard(page, 'npm run reading:long-output');
    const summary = card.locator('summary');
    await expect(summary).toContainText('输出更新');
    const summaryStyle = await summary.evaluate(element => {
      const style = getComputedStyle(element);
      return { alignItems: style.alignItems, minHeight: style.minHeight, height: element.getBoundingClientRect().height };
    });
    expect(summaryStyle.alignItems).toBe('baseline');
    expect(summaryStyle.minHeight).toBe('40px');
    expect(summaryStyle.height).toBeGreaterThanOrEqual(40);
    for (const selector of ['[class*="toolPhase"]', '[class*="toolTiming"]']) {
      await expect(card.locator(selector).first()).toBeVisible();
      expect(await card.locator(selector).first().evaluate(element => getComputedStyle(element).paddingLeft)).toBe('32px');
    }

    for (let index = 1; index < defaultBatches().length; index++) {
      expect(server.advanceStream()).toBe(true);
      await expect(page.getByText(`阅读连续性检查 ${index + 1}`, { exact: false })).toBeVisible();
    }
    server.finishStream();
    await expect(page.getByText('阅读连续性检查 8', { exact: false })).toBeVisible();
    await summary.click();
    await expect(card).toHaveAttribute('open', '');
    for (const selector of ['[class*="finishedPhase"]', '[class*="finishedTiming"]']) {
      await expect(card.locator(selector)).toBeVisible();
      expect(await card.locator(selector).evaluate(element => getComputedStyle(element).paddingLeft)).toBe('32px');
    }
  } finally {
    await server.close();
  }
});

test('流式输出：首批流结束前可见，显式推进全部批次并完成消息/RunView/Session 收尾', async ({ page }) => {
  const batches = defaultBatches();
  const server = startConversationServer({ live: true, batches });
  await server.listen();
  try {
    await openConversation(page, { port: server.port });
    await expect(page.getByText(BATCH1_HEAD, { exact: false })).toBeVisible({ timeout: 5000 });
    expect(server.state.finished, '首批内容渲染时流尚未结束').toBe(false);
    expect(server.state.streamed).toBe(1);

    for (let index = 1; index < batches.length; index++) {
      expect(server.advanceStream(), `推进第 ${index + 1} 批`).toBe(true);
      await expect(page.getByText(`阅读连续性检查 ${index + 1}`, { exact: false })).toBeVisible();
      expect(server.state.finished).toBe(false);
    }
    expect(server.state.streamed).toBe(batches.length);

    const finalRun = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/scopes/${SCOPE}/runs/${RUN_ID}` && response.ok());
    const finalSession = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/scopes/${SCOPE}/sessions/${SESSION_ID}` && response.ok());
    server.finishStream();
    const [runResponse, sessionResponse] = await Promise.all([finalRun, finalSession]);
    const run = await runResponse.json();
    const session = await sessionResponse.json();
    expect(run.status).toBe('completed');
    expect(run.done).toBe(true);
    expect(run.messages.find((row: { id: string }) => row.id === 'assistant-live').content).toBe(batches.join(''));
    expect(session.busy).toBe(false);
    expect(session.active_run_id).toBeNull();
    expect(session.live.done).toBe(true);
    expect(session.messages.find((row: { id: string }) => row.id === 'assistant-live').content).toBe(batches.join(''));

    const answer = page.locator('[data-message-id="assistant-live"]');
    await expect(answer).toBeVisible();
    for (let index = 0; index < batches.length; index++) {
      await expect(answer).toContainText(`阅读连续性检查 ${index + 1}`);
    }
    await expect(answer).not.toContainText('未完成内容');
    await expect(answer).not.toContainText('正在生成');
    expect(server.state.finished).toBe(true);

    // 离开再回来后只使用最终 Session 快照，完整回答仍应存在。
    await page.getByRole('button', { name: '切换目标会话 A', exact: true }).click();
    await expect(page.getByText('切换目标会话的确定回答')).toBeVisible();
    const prepare = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/scopes/${SCOPE}/sessions/${SESSION_ID}/prepare` && response.ok());
    await page.getByRole('button', { name: '阅读基线会话 A', exact: true }).click();
    await prepare;
    await expect(page.locator('[data-message-id="assistant-live"]')).toContainText('阅读连续性检查 8');
    await expect(page.locator('[data-message-id="assistant-live"]')).not.toContainText('未完成内容');
    expect(server.calls.some(call => call.includes('UNMOCKED'))).toBe(false);
  } finally {
    await server.close();
  }
});
