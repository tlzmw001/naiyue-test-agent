import { expect, test } from '@playwright/test';

// Spec143BB 最小组合验证：受控 DropdownMenu.Sub + 公开 onEscapeKeyDown 逐层关闭。
// 测试专用组合页（e2e-harness/），不进入产品；此处只验证公开 API 的机制。
test.skip(({ viewport }) => (viewport?.width ?? 0) <= 760, '组合场景按桌面验收');

async function openPilot(page: import('@playwright/test').Page) {
  await page.goto('http://127.0.0.1:4573/e2e-harness/index.html');
  await page.getByRole('button', { name: '打开主菜单' }).click();
  await expect(page.getByRole('menu', { name: '主菜单' })).toBeVisible();
}

test('受控子菜单：ArrowRight 展开，Escape 只关子层并恢复触发器焦点', async ({ page }) => {
  await openPilot(page);
  const subTrigger = page.getByRole('menuitem', { name: '发送后滚动' });
  await subTrigger.focus();
  await page.keyboard.press('ArrowRight');
  const submenu = page.getByRole('menu', { name: '发送后滚动' });
  await expect(submenu).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(submenu).not.toBeVisible();
  await expect(page.getByRole('menu', { name: '主菜单' })).toBeVisible();
  await expect(subTrigger).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(page.getByRole('menu', { name: '主菜单' })).not.toBeVisible();
  await expect(page.getByRole('button', { name: '打开主菜单' })).toBeFocused();
});

test('受控子菜单：hover 展开，子项选择一次回调且关闭菜单', async ({ page }) => {
  await openPilot(page);
  await page.getByRole('menuitem', { name: '发送后滚动' }).hover();
  await expect(page.getByRole('menu', { name: '发送后滚动' })).toBeVisible();
  const opt = page.getByRole('menuitem', { name: '选项一' });
  await opt.click();
  const selects = await page.evaluate(() => (window as unknown as { __pilot: { selects: number } }).__pilot.selects);
  expect(selects).toBe(1);
  await expect(page.getByRole('menu', { name: '主菜单' })).not.toBeVisible();
  await expect(page.getByRole('button', { name: '打开主菜单' })).toBeFocused();
});

test('原生 dialog 内嵌菜单：Portal container 使菜单在 top layer 内可用', async ({ page }) => {
  await page.goto('http://127.0.0.1:4573/e2e-harness/index.html');
  await page.getByRole('button', { name: '打开弹窗' }).click();
  const dialog = page.getByRole('dialog', { name: '组合弹窗' });
  await expect(dialog).toBeVisible();
  await page.getByRole('button', { name: '弹窗内菜单' }).click();
  const menu = page.getByRole('menu', { name: '弹窗内菜单' });
  await expect(menu).toBeVisible();
  // 菜单在原生 top layer 内可操作：进入子菜单并选择。
  await page.getByRole('menuitem', { name: '弹窗内子菜单' }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('menu', { name: '弹窗内子菜单' })).toBeVisible();
  const opt = page.getByRole('menuitem', { name: '选项一' });
  await opt.click();
  await expect(menu).not.toBeVisible();
});
