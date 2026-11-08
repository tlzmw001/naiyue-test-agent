import { expect, test } from '@playwright/test';
import { baseMocks, openHome } from './fixtures/service';

// Spec143BA 阶段 2：模态打开时的背景滚动受控与模态自身滚动。
// 不以"加了一条滚动锁 CSS"作为结论，直接核对真实滚动容器的 scrollTop。
test.skip(({ viewport }) => (viewport?.width ?? 0) <= 760, '按桌面布局验收');

async function sessionListScroller(page: import('@playwright/test').Page) {
  return page.evaluateHandle(() => {
    const nav = document.querySelector('aside[aria-label="会话导航"] nav');
    const candidates = nav ? [nav, ...nav.querySelectorAll<HTMLElement>('div')] : [];
    return candidates
      .find(el => el.scrollHeight > el.clientHeight + 40 && getComputedStyle(el).overflowY.includes('auto')) ?? null;
  });
}

test('搜索模态打开时背景会话列表不随滚轮滚动，模态内结果列表可滚', async ({ page }) => {
  await openHome(page, baseMocks());
  await page.getByRole('button', { name: '历史会话 24 B' }).waitFor();
  const scroller = await sessionListScroller(page);

  // 无模态时背景列表确实响应滚轮（可滚动性的正向对照）。
  await page.mouse.move(130, 500);
  await page.mouse.wheel(0, 300);
  await page.waitForTimeout(200);
  const freeScroll = await scroller.evaluate(el => el.scrollTop);
  expect(freeScroll, '无模态时背景列表应能滚动').toBeGreaterThan(0);

  await page.getByRole('button', { name: '搜索会话' }).click();
  await expect(page.getByRole('combobox', { name: '搜索会话标题' })).toBeFocused();
  const before = await scroller.evaluate(el => el.scrollTop);

  // 模态打开后，滚轮落在模态外的背景区域，背景不应滚动。
  await page.mouse.move(130, 500);
  await page.mouse.wheel(0, -600);
  await page.waitForTimeout(200);
  const after = await scroller.evaluate(el => el.scrollTop);
  expect(after, '模态打开时背景列表 scrollTop 不应变化').toBe(before);

  // 模态内部用真实滚轮滚动：鼠标移到结果列表中央并向下滚，scrollTop 应增加。
  const results = page.getByRole('listbox', { name: '匹配会话' });
  const bounds = await results.boundingBox();
  if (!bounds) throw new Error('结果列表不可见');
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  const beforeInner = await results.evaluate(el => el.scrollTop);
  await page.mouse.wheel(0, 300);
  await page.waitForTimeout(200);
  const afterInner = await results.evaluate(el => el.scrollTop);
  expect(afterInner, '模态内部结果列表应能随用户滚轮滚动').toBeGreaterThan(beforeInner);
});
