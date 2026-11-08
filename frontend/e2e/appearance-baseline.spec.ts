import { expect, test } from '@playwright/test';
import { baseMocks, openHome } from './fixtures/service';

// Spec143BA 阶段 2 迁移前的外观基线：SessionSearch 与 SessionMenu 是迁移对象，
// 迁移后按同场景截图对比。产物存 build/spec143ba/，只截图不承担行为断言。
// 外观核对工具：仅在显式提供 APPEARANCE_DIR 时运行，避免常规回归覆盖既有截图。
const dir = process.env.APPEARANCE_DIR ?? '';
test.skip(!dir, '设置 APPEARANCE_DIR=<目录> 后运行外观截图');

async function settle(page: import('@playwright/test').Page) {
  await expect(page.getByRole('button', { name: '验收会话 A' })).toBeVisible();
}

test('桌面浅色：首页 / 搜索模态 / 会话菜单 / 重命名模态', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 760, '桌面基线');
  await openHome(page, baseMocks());
  await settle(page);
  await page.screenshot({ path: `${dir}/desktop-light-home.png` });

  await page.getByRole('button', { name: '搜索会话' }).click();
  await expect(page.getByRole('combobox', { name: '搜索会话标题' })).toBeFocused();
  await page.screenshot({ path: `${dir}/desktop-light-search.png` });
  await page.keyboard.press('Escape');

  const row = page.getByRole('button', { name: '验收会话 A' });
  await row.click({ button: 'right' });
  await expect(page.getByRole('menu', { name: '会话操作' })).toBeVisible();
  await page.screenshot({ path: `${dir}/desktop-light-menu.png` });

  await page.getByRole('menuitem', { name: '重命名' }).click();
  await expect(page.getByRole('dialog', { name: '重命名会话' })).toBeVisible();
  await page.screenshot({ path: `${dir}/desktop-light-rename.png` });
});

test('桌面深色：搜索模态与重命名模态', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 760, '桌面基线');
  await openHome(page, baseMocks());
  await settle(page);
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  await page.getByRole('button', { name: '搜索会话' }).click();
  await expect(page.getByRole('combobox', { name: '搜索会话标题' })).toBeFocused();
  await page.screenshot({ path: `${dir}/desktop-dark-search.png` });
  await page.keyboard.press('Escape');

  await page.getByRole('button', { name: '验收会话 A' }).click({ button: 'right' });
  await page.getByRole('menuitem', { name: '重命名' }).click();
  await expect(page.getByRole('dialog', { name: '重命名会话' })).toBeVisible();
  await page.screenshot({ path: `${dir}/desktop-dark-rename.png` });
});

test('窄屏浅色：抽屉与搜索模态', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) > 760, '窄屏基线');
  await openHome(page, baseMocks());
  await settle(page);
  await page.screenshot({ path: `${dir}/mobile-light-home.png` });

  await page.getByRole('button', { name: '打开会话栏' }).click();
  await expect(page.getByRole('button', { name: '关闭会话栏' })).toBeVisible();
  await page.screenshot({ path: `${dir}/mobile-light-drawer.png` });

  await page.getByRole('button', { name: '搜索会话' }).click();
  await expect(page.getByRole('combobox', { name: '搜索会话标题' })).toBeFocused();
  await page.screenshot({ path: `${dir}/mobile-light-search.png` });
});

test('窄屏深色：抽屉与搜索模态', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) > 760, '窄屏基线');
  await openHome(page, baseMocks());
  await settle(page);
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  await page.getByRole('button', { name: '打开会话栏' }).click();
  await expect(page.getByRole('button', { name: '关闭会话栏' })).toBeVisible();
  await page.screenshot({ path: `${dir}/mobile-dark-drawer.png` });

  await page.getByRole('button', { name: '搜索会话' }).click();
  await expect(page.getByRole('combobox', { name: '搜索会话标题' })).toBeFocused();
  await page.screenshot({ path: `${dir}/mobile-dark-search.png` });
});
