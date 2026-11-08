import { expect, test } from '@playwright/test';
import { baseMocks, openHome } from './fixtures/service';

// Spec143BB 基线：ThemeMenu 自研实现的行为合同，Radix 迁移后复用同一断言。
test.skip(({ viewport }) => (viewport?.width ?? 0) <= 760, '菜单交互按桌面布局验收');

const trigger = (page: import('@playwright/test').Page) => page.getByRole('button', { name: /主题：/ });

test('主题菜单：打开聚焦选中项，方向键循环，Home/End 定位', async ({ page }) => {
  await openHome(page, baseMocks());
  await trigger(page).click();
  const menu = page.getByRole('menu', { name: '主题选择' });
  await expect(menu).toBeVisible();
  const checked = page.getByRole('menuitemradio', { name: '跟随系统' });
  await expect(checked).toBeFocused();

  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('menuitemradio', { name: '浅色' })).toBeFocused();
  await page.keyboard.press('End');
  await expect(page.getByRole('menuitemradio', { name: '深色' })).toBeFocused();
  await page.keyboard.press('Home');
  await expect(checked).toBeFocused();
  await page.keyboard.press('ArrowUp');
  await expect(page.getByRole('menuitemradio', { name: '深色' })).toBeFocused();
});

test('主题菜单：选择后切换主题、关闭并恢复焦点', async ({ page }) => {
  await openHome(page, baseMocks());
  const button = trigger(page);
  await button.click();
  await page.getByRole('menuitemradio', { name: '深色', exact: true }).click();
  await expect(page.getByRole('menu', { name: '主题选择' })).not.toBeVisible();
  await expect(page.evaluate(() => document.documentElement.dataset.theme)).resolves.toBe('dark');
  await expect(button).toBeFocused();
});

test('主题菜单：Escape 关闭恢复焦点，外部点击关闭', async ({ page }) => {
  await openHome(page, baseMocks());
  const button = trigger(page);
  await button.click();
  await expect(page.getByRole('menu', { name: '主题选择' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('menu', { name: '主题选择' })).not.toBeVisible();
  await expect(button).toBeFocused();

  await button.click();
  await expect(page.getByRole('menu', { name: '主题选择' })).toBeVisible();
  // Radix 的外部点击监听经 setTimeout 注册；用双 RAF 等浏览器帧确保挂载完成，而非固定 sleep。
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(undefined)))));
  await page.mouse.click(400, 300);
  await expect(page.getByRole('menu', { name: '主题选择' })).not.toBeVisible();
});

test('主题菜单：键盘打开，选择非首项后重开仍聚焦当前选中项', async ({ page }) => {
  await openHome(page, baseMocks());
  const button = trigger(page);
  await button.focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('menu', { name: '主题选择' })).toBeVisible();
  await page.getByRole('menuitemradio', { name: '深色', exact: true }).click();
  await expect(page.getByRole('menu', { name: '主题选择' })).not.toBeVisible();

  await button.click();
  await expect(page.getByRole('menuitemradio', { name: '深色', exact: true })).toBeFocused();
});

test('主题菜单：靠近窗口边缘与 resize 后定位不溢出', async ({ page }) => {
  await page.setViewportSize({ width: 500, height: 500 });
  await openHome(page, baseMocks());
  await trigger(page).click();
  const menu = page.getByRole('menu', { name: '主题选择' });
  await expect(menu).toBeVisible();
  let bounds = await menu.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(500);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(500);

  await page.setViewportSize({ width: 375, height: 812 });
  await expect(menu).toBeVisible();
  await expect.poll(async () => (await menu.boundingBox())!.x).toBeGreaterThanOrEqual(0);
  await expect.poll(async () => {
    const next = await menu.boundingBox();
    return next!.x + next!.width;
  }).toBeLessThanOrEqual(375);
  bounds = await menu.boundingBox();
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(812);
});

test('主题菜单：外部点击后焦点落在被点击元素，不强制回触发器', async ({ page }) => {
  await openHome(page, baseMocks());
  const button = trigger(page);
  await button.click();
  await expect(page.getByRole('menu', { name: '主题选择' })).toBeVisible();
  await page.getByRole('textbox', { name: '消息' }).click();
  await expect(page.getByRole('menu', { name: '主题选择' })).not.toBeVisible();
  await expect(page.getByRole('textbox', { name: '消息' })).toBeFocused();
});
