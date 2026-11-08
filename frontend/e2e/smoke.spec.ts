import { expect, test } from '@playwright/test';
import { baseMocks, openHome } from './fixtures/service';

// 探查用例：确认首屏在纯拦截数据下渲染，并列出未声明的接口请求，用于补齐 fixtures。
test('smoke: 首页在拦截数据下加载', async ({ page }) => {
  const service = await openHome(page, baseMocks());
  if (service.unexpected.length) console.log('UNMOCKED REQUESTS:\n' + service.unexpected.join('\n'));
  await expect(page.getByRole('button', { name: '打开辅助侧栏' })).toBeVisible({ timeout: 15_000 });
  service.expectNoUnexpectedRequests();
});
