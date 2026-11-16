import { expect, test } from '@playwright/test';
import { baseMocks, json, openHome, project, sessionId } from './fixtures/service';

// 探查用例：确认首屏在纯拦截数据下渲染，并列出未声明的接口请求，用于补齐 fixtures。
test('smoke: 首页在拦截数据下加载', async ({ page }) => {
  const service = await openHome(page, baseMocks());
  if (service.unexpected.length) console.log('UNMOCKED REQUESTS:\n' + service.unexpected.join('\n'));
  await expect(page.getByRole('button', { name: '打开辅助侧栏' })).toBeVisible({ timeout: 15_000 });
  service.expectNoUnexpectedRequests();
});


test('上下文校准：当前容量与原始构成分别展示', async ({ page, viewport }, info) => {
  const estimate = { status: 'available', estimated_input_tokens: 20000, groups_status: 'available',
    image_count: 0, estimator: 'framework_approximate', context_window_tokens: 100000,
    max_output_tokens: 10000, compaction_reserve_tokens: 20000, trigger_threshold_tokens: 80000,
    context_window_source: 'config', max_output_source: 'config', compaction_reserve_source: 'config',
    groups: [{ id: 'user', tokens: 5000, count: 1 }, { id: 'tool_results', tokens: 15000, count: 2 }],
    calibration: { method: 'ratio_estimate', calibrated_input_tokens: 40000, invalidation_reason: null } };
  const service = await openHome(page, [json(`/api/v1/scopes/${project}/sessions/${sessionId}/context`, {
    session_id: sessionId, current: { basis: 'next_request', model_id: 'A', estimate },
    latest_request: { version: 2, model_call_id: 'call', model_id: 'A', run_id: 'r',
      recorded_at: '2026-11-16T10:00:00Z', status: 'returned', estimate,
      usage: { input_tokens: 50000, output_tokens: 100, cache_read_tokens: 0, cache_write_tokens: null } },
  }), ...baseMocks()]);
  if ((viewport?.width ?? 1440) <= 760) await page.getByRole('button', { name: '打开会话栏' }).click();
  await page.getByRole('button', { name: '验收会话 A' }).click();
  await page.getByRole('button', { name: '查看上下文构成，预估占用40.0%' }).click();
  const panel = page.getByRole('region', { name: '上下文' });
  await expect(panel.getByText('校准预估', { exact: true })).toBeVisible();
  await expect(panel.getByText('输入预估 40,000')).toBeVisible();
  await expect(panel.getByText('50,000', { exact: true })).toBeVisible();
  await expect(panel.getByText('75.0%', { exact: true })).toBeVisible();
  await expect(panel.getByText('各项均为原始本地估算')).toBeVisible();
  await page.screenshot({ path: `../build/spec143bl-s3-context-${info.project.name}.png`, fullPage: true });
  await panel.getByRole('tab', { name: '最近请求' }).click();
  await expect(panel.getByText('50,000', { exact: true })).toBeVisible();
  await expect(panel.getByText('同一次发送前的原始估算：≈ 20,000 tokens。')).toBeVisible();
  await expect(panel.getByText('同一次发送前的校准预估：≈ 40,000 tokens。')).toBeVisible();
  service.expectNoUnexpectedRequests();
  expect(service.commandPosts()).toEqual([]);
});
