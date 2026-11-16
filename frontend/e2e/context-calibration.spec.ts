import { expect, test } from '@playwright/test';
import type { ContextEstimate, ContextSnapshot } from '../src/contextTypes';
import { baseMocks, json, openHome, project, sessionId } from './fixtures/service';

// 合成 API 夹具验收：真实浏览器渲染，不是供应商计量效果证据。
const base: ContextEstimate = {
  status: 'available', estimated_input_tokens: 20000, groups_status: 'available',
  image_count: 0, estimator: 'framework_approximate', context_window_tokens: 100000,
  max_output_tokens: 10000, compaction_reserve_tokens: 20000, trigger_threshold_tokens: 80000,
  context_window_source: 'config', max_output_source: 'config', compaction_reserve_source: 'config',
  groups: [{ id: 'user', tokens: 5000, count: 1 }, { id: 'tool_results', tokens: 15000, count: 2 }],
};

const cases = [
  { name: 'legacy', label: '历史原始估算', total: 20000, version: 1, estimate: base },
  { name: 'ratio-high', label: '校准预估', total: 80001, version: 2,
    estimate: { ...base, calibration: { method: 'ratio_estimate', calibrated_input_tokens: 80001, invalidation_reason: null } } },
  { name: 'ratio-low', label: '校准预估', total: 10000, version: 2,
    estimate: { ...base, calibration: { method: 'ratio_estimate', calibrated_input_tokens: 10000, invalidation_reason: null } } },
  { name: 'uncalibrated', label: '未校准预估', total: 20000, version: 2,
    estimate: { ...base, calibration: { method: 'uncalibrated', calibrated_input_tokens: 20000, invalidation_reason: 'no_samples' } } },
  { name: 'unavailable', total: null, version: 2,
    estimate: { ...base, status: 'unavailable', estimated_input_tokens: null, groups_status: 'unavailable', groups: [],
      calibration: { method: 'unavailable', calibrated_input_tokens: null, invalidation_reason: 'raw_estimate_unavailable' } } },
  { name: 'post-compaction', label: '校准预估', total: 6000, version: 2, noSnapshot: true,
    estimate: { ...base, estimated_input_tokens: 3000, groups: [{ id: 'summary', tokens: 3000, count: 1 }],
      calibration: { method: 'ratio_estimate', calibrated_input_tokens: 6000, invalidation_reason: null } } },
] as const;

for (const scenario of cases) {
  test(`合成夹具：上下文 ${scenario.name}`, async ({ page, viewport }, info) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    const estimate = scenario.estimate as ContextEstimate;
    const snapshot = 'noSnapshot' in scenario ? null : {
      version: scenario.version, model_call_id: 'fixture-call', model_id: 'A', run_id: 'fixture-run',
      recorded_at: '2026-11-16T10:00:00Z', status: 'returned', estimate,
      usage: { input_tokens: 50000, output_tokens: 100, cache_read_tokens: 0, cache_write_tokens: null },
    } as ContextSnapshot;
    const service = await openHome(page, [json(`/api/v1/scopes/${project}/sessions/${sessionId}/context`, {
      session_id: sessionId, current: { basis: 'next_request', model_id: 'A', estimate }, latest_request: snapshot,
    }), ...baseMocks()]);
    if ((viewport?.width ?? 1440) <= 760) await page.getByRole('button', { name: '打开会话栏' }).click();
    await page.getByRole('button', { name: '验收会话 A' }).click();
    const percent = scenario.total == null ? null : (scenario.total / 100000 * 100).toFixed(1);
    const trigger = page.getByRole('button', { name: percent == null ? '查看上下文构成' : `查看上下文构成，预估占用${percent}%`, exact: true });
    if (scenario.total != null && scenario.total > 80000) await expect(trigger).toHaveAttribute('data-threshold', 'reached');
    else await expect(trigger).not.toHaveAttribute('data-threshold', 'reached');
    await trigger.click();
    const panel = page.getByRole('region', { name: '上下文' });
    if (scenario.total == null) {
      await expect(panel.getByText('当前预估暂不可用。最近请求的已记录用量仍可查看。')).toBeVisible();
      await expect(panel.getByText('输入预估 0')).toHaveCount(0);
    } else {
      await expect(panel.getByText(scenario.label, { exact: true })).toBeVisible();
      await expect(panel.getByText(`输入预估 ${scenario.total.toLocaleString('en-US')}`, { exact: true })).toBeVisible();
      await expect(panel.locator('strong', { hasText: Math.max(0, 90000 - scenario.total).toLocaleString('en-US') })).toBeVisible();
      await expect(panel.getByText('各项均为原始本地估算')).toBeVisible();
      await expect(panel.getByText(scenario.name === 'post-compaction' ? '100.0%' : '75.0%', { exact: true })).toBeVisible();
      if (scenario.name === 'post-compaction') await expect(panel.getByText('历史压缩摘要 · 1 条')).toBeVisible();
    }
    await expect(page.locator('body')).toHaveJSProperty('scrollWidth', viewport!.width);
    await page.screenshot({ path: `../build/spec143bl-s4-fixture-${scenario.name}-${info.project.name}.png`, fullPage: true });
    await panel.getByRole('tab', { name: '最近请求' }).click();
    if (!snapshot) {
      await expect(panel.getByText('暂无已记录的模型请求。历史请求未保存的构成不会补造。')).toBeVisible();
    } else {
      await expect(panel.getByText('50,000', { exact: true })).toBeVisible();
      await expect(panel.getByText(`同一次发送前的原始估算：≈ ${estimate.estimated_input_tokens == null ? '未提供' : '20,000'} tokens。`)).toBeVisible();
      if (snapshot.version === 1) await expect(panel.getByText(/同一次发送前的校准预估/)).toHaveCount(0);
      else if (scenario.total != null) await expect(panel.getByText(`同一次发送前的${scenario.label}：≈ ${scenario.total.toLocaleString('en-US')} tokens。`)).toBeVisible();
    }
    service.expectNoUnexpectedRequests();
    expect(service.commandPosts()).toEqual([]);
    expect(errors).toEqual([]);
  });
}
