import { expect, test } from '@playwright/test';
import { openCloudTask } from './fixtures/service';

// 期望行为依据：浮层菜单在点击外部时应关闭（与其余菜单组件一致），
// 且外部点击不应误触菜单项或打开文件选择。
test.skip(({ browserName, viewport }) => browserName !== 'chromium' || (viewport?.width ?? 0) <= 760, '外部点击坐标依赖桌面布局');

test('附件菜单点击外部后关闭，且不触发文件选择', async ({ page }) => {
  const service = await openCloudTask(page);
  await page.getByRole('button', { name: '添加附件' }).click();
  const menu = page.getByRole('button', { name: '添加文件或文件夹' });
  await expect(menu).toBeVisible();

  // 点击会话主区的空白区域（当前消息列表为空，安全的中性位置）。
  await page.mouse.click(400, 300);

  await expect(menu).not.toBeVisible();
  await expect(page.getByRole('dialog', { name: '选择上传文件' })).not.toBeVisible();
  service.expectNoUnexpectedRequests();
});

test('点击菜单内部项正常打开文件选择', async ({ page }) => {
  const service = await openCloudTask(page);
  await page.getByRole('button', { name: '添加附件' }).click();
  await page.getByRole('button', { name: '添加文件或文件夹' }).click();
  await expect(page.getByRole('dialog', { name: '选择上传文件' })).toBeVisible();
  service.expectNoUnexpectedRequests();
});
