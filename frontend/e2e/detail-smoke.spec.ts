import { expect, test } from '@playwright/test';
import { baseMocks, cloudTaskMocks, openHome } from './fixtures/service';

// 探查用例：任务详情在纯拦截数据下渲染；无论成败都列出未声明的接口请求，用于补齐 fixtures。
test.skip(({ viewport }) => (viewport?.width ?? 0) <= 760, '详情布局按桌面探查');
test('smoke: 云端任务详情在拦截数据下加载', async ({ page }) => {
  const service = await openHome(page, [...baseMocks(), ...cloudTaskMocks()]);
  try {
    const prepare = page.waitForResponse(`**/sessions/*/prepare`);
    await page.getByRole('button', { name: '验收会话 A' }).click();
    await prepare;
    await page.getByRole('button', { name: '指令与功能' }).click();
    await page.getByRole('menuitem', { name: '云端任务' }).click();
    await page.getByRole('button', { name: /任务 aaaaaaaa/ }).click();
    await expect(page.getByRole('button', { name: '停止后台任务' })).toBeVisible();
    service.expectNoUnexpectedRequests();
  } finally {
    if (service.unexpected.length) console.log('UNMOCKED REQUESTS:\n' + service.unexpected.join('\n'));
  }
});
