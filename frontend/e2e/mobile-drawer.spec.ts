import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { baseMocks, openHome } from './fixtures/service';

// 期望行为依据：窄屏下会话导航是浮层抽屉，应支持 Escape 关闭（与 ActivitySidebar
// 抽屉一致）、遮罩点击关闭，且关闭后焦点回到「打开会话栏」入口按钮。当前实现
// （Sidebar 移动抽屉无 Escape/焦点恢复处理）预期在本用例上失败，作为复现证据。
test.skip(({ browserName, viewport }) => browserName !== 'chromium' || (viewport?.width ?? 0) > 760, '仅窄屏抽屉场景');

// 抽屉关闭 = 遮罩按钮（仅 open 态渲染）消失；侧栏本体关闭后只是移出屏幕，
// bounding box 仍在，不能作为关闭信号。
const drawerBackdrop = (page: Page) => page.getByRole('button', { name: '关闭会话栏' });

async function openDrawer(page: Page) {
  await openHome(page, baseMocks());
  const trigger = page.getByRole('button', { name: '打开会话栏' });
  await trigger.click();
  await expect(page.getByRole('button', { name: '验收会话 A' })).toBeVisible();
  await expect(drawerBackdrop(page)).toBeVisible();
  return trigger;
}

test('Escape 关闭会话导航抽屉，焦点回到入口', async ({ page }) => {
  const trigger = await openDrawer(page);
  await page.keyboard.press('Escape');
  await expect(drawerBackdrop(page)).not.toBeVisible();
  await expect(trigger).toBeFocused();
});

test('点击遮罩关闭抽屉后，焦点回到入口按钮', async ({ page }) => {
  const trigger = await openDrawer(page);
  // 视口 375px，点击抽屉右侧的遮罩空白区域。
  await page.mouse.click(368, 400);
  await expect(drawerBackdrop(page)).not.toBeVisible();
  await expect(trigger).toBeFocused();
});
