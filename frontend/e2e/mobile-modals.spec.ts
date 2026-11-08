import { expect, test } from '@playwright/test';
import { baseMocks, openHome } from './fixtures/service';

// Spec143BA A.2：刚迁移模态的窄屏代表性行为（桌面已有失败重试/busy/请求次数断言）。
// 抽屉、模态与最上层 Escape 的层级规则在此验证。
test.skip(({ browserName, viewport }) => browserName !== 'chromium' || (viewport?.width ?? 0) > 760, '窄屏模态场景');

const drawerBackdrop = (page: import('@playwright/test').Page) => page.getByRole('button', { name: '关闭会话栏' });

async function openDrawerAndSearch(page: import('@playwright/test').Page) {
  await openHome(page, baseMocks());
  await page.getByRole('button', { name: '打开会话栏' }).click();
  await page.getByRole('button', { name: '搜索会话' }).click();
  const search = page.getByRole('combobox', { name: '搜索会话标题' });
  await expect(search).toBeFocused();
  return search;
}

test('窄屏搜索：打开、取消恢复焦点、选择会话', async ({ page }) => {
  const search = await openDrawerAndSearch(page);
  await page.keyboard.press('Escape');
  await expect(search).toHaveCount(0);
  await expect(page.getByRole('button', { name: '搜索会话' })).toBeFocused();

  await page.getByRole('button', { name: '搜索会话' }).click();
  const again = page.getByRole('combobox', { name: '搜索会话标题' });
  await expect(again).toBeFocused();
  await page.getByRole('option', { name: /验收会话/ }).click();
  await expect(again).toHaveCount(0);
});

test('窄屏重命名模态：打开与取消，Escape 只关最上层', async ({ page }) => {
  await openHome(page, baseMocks());
  await page.getByRole('button', { name: '打开会话栏' }).click();
  await expect(drawerBackdrop(page)).toBeVisible();

  await page.getByRole('button', { name: '验收会话的更多操作' }).click();
  await page.getByRole('menuitem', { name: '重命名' }).click();
  const dialog = page.getByRole('dialog', { name: '重命名会话' });
  await expect(dialog).toBeVisible();

  // 第一次 Escape：只关模态，抽屉保持打开。
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(drawerBackdrop(page)).toBeVisible();

  // 模态关闭后再按 Escape：关闭抽屉并恢复入口焦点。
  await page.keyboard.press('Escape');
  await expect(drawerBackdrop(page)).not.toBeVisible();
  await expect(page.getByRole('button', { name: '打开会话栏' })).toBeFocused();
});

test('窄屏删除确认模态打开与取消', async ({ page }) => {
  await openHome(page, baseMocks());
  await page.getByRole('button', { name: '打开会话栏' }).click();
  await page.getByRole('button', { name: '验收会话的更多操作' }).click();
  await page.getByRole('menuitem', { name: '删除' }).click();
  const dialog = page.getByRole('dialog', { name: '删除会话' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: '取消' }).click();
  await expect(dialog).not.toBeVisible();
  await expect(drawerBackdrop(page)).toBeVisible();
});
