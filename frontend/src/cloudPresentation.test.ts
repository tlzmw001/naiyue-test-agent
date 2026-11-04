import { expect, it } from 'vitest';
import { ApiError } from './api';
import { cloudError } from './cloudPresentation';
it.each([
  ['cloud_delivery_preflight_changed', '重新预检'],
  ['cloud_delivery_baseline_changed', '应用基线'],
  ['cloud_delivery_destination_busy', '另一项取回'],
  ['cloud_delivery_path_unsafe', '路径'],
  ['cloud_result_identity_mismatch', '任务不一致'],
])('explains %s at the result-return boundary', (code, text) => {
  expect(cloudError(new ApiError(code))).toContain(text);
});
it('explains a locally detected identity mismatch without showing a generic network error', () => {
  expect(cloudError(new Error('cloud_result_identity_mismatch'))).toContain('任务不一致');
});
