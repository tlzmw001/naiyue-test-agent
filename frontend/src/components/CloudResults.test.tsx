import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import CloudResults from './CloudResults';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding } from '../cloudTypes';
afterEach(cleanup);
const binding = { binding_id: 'B', revision: 8, cloud: { artifact_operation_id: null } } as CloudBinding;
const file = { role: 'artifact', artifact: { artifact_id: 'F', relative_name: 'report.html', size: 23, sha256: 'a'.repeat(64) } };
function api(phase = 'available') {
  return { results: vi.fn().mockResolvedValue([{ result_id: 'R', source_run_id: 'run12345', phase, revision: 2, outcome: 'failed' }]),
    result: vi.fn().mockResolvedValue({ plan: { result_id: 'R', input_id: 'i'.repeat(64) }, phase, revision: 2, boundary: { outcome: 'failed' }, bundle: { files: [file] } }),
    changes: vi.fn().mockResolvedValue({ changes: [{ path: 'new.py', kind: 'added', portable: true }], total: 1, next_offset: null }),
    resultPreview: vi.fn().mockResolvedValue({ text: '<script>bad()</script>', binary: false, complete: true, next_offset: 23 }),
    resultAction: vi.fn().mockResolvedValue({}), downloadResult: vi.fn().mockResolvedValue(undefined) } as unknown as CloudApi;
}
it('shows actual files, separates failed execution from available result and escapes preview', async () => {
  const cloud = api(); const view = render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: /运行 run12345/ }));
  expect(await screen.findByText('新增 · new.py')).toBeTruthy();
  expect(screen.getAllByText(/执行失败/).length).toBeGreaterThan(0);
  expect(screen.getByText(/文件完整性已核对/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '预览' }));
  expect(await screen.findByText('<script>bad()</script>')).toBeTruthy(); expect(view.container.querySelector('script')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '下载' })); expect(cloud.downloadResult).toHaveBeenCalledWith('S', 'B', 'R', file);
});
it('requires explicit confirmation before abandoning and sends fixed result revision', async () => {
  const cloud = api('failed'); render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: /运行 run12345/ }));
  fireEvent.click(await screen.findByRole('button', { name: '放弃本轮结果' })); expect(cloud.resultAction).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '确认放弃' }));
  expect(cloud.resultAction).toHaveBeenCalledWith('S', 'B', 'R', expect.objectContaining({ action: 'abandon', expected_revision: 2, expected_binding_revision: 8 }));
});
it('does not dispatch or invent outputs for an empty result list', async () => {
  const cloud = api(); vi.mocked(cloud.results).mockResolvedValue([]); render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />);
  expect(await screen.findByText('暂无已提交运行的结果。')).toBeTruthy(); expect(cloud.resultAction).not.toHaveBeenCalled(); expect(cloud.result).not.toHaveBeenCalled();
});
it('ignores an old result page after selecting another result', async () => {
  const cloud = api(); let oldPage!: (value: { changes: { path: string; kind: string; portable: boolean }[]; total: number; next_offset: null }) => void;
  vi.mocked(cloud.results).mockResolvedValue(['A', 'B'].map(result_id => ({ result_id, source_run_id: result_id + '1234567', phase: 'available', revision: 2, outcome: 'completed', error_code: null, captured_at: 1, files_count: 2 })));
  vi.mocked(cloud.result).mockImplementation(async (_s, _b, rid) => ({ plan: { result_id: rid, source_run_id: 'run', input_id: 'i'.repeat(64) }, phase: 'available', revision: 2, error_code: null, boundary: { outcome: 'completed' }, bundle: { captured_at: 1, files: [] } }));
  vi.mocked(cloud.changes).mockImplementation(async (_s, _b, rid, offset) => {
    if (rid === 'A' && offset) return new Promise(resolve => { oldPage = resolve; });
    return { changes: [{ path: rid + '-only.py', kind: 'added', portable: true }], total: 2, next_offset: rid === 'A' ? 1 : null };
  });
  render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: /运行 A1234567/ }));
  fireEvent.click(await screen.findByRole('button', { name: '加载更多代码变化' }));
  fireEvent.click(screen.getByRole('button', { name: /运行 B1234567/ }));
  expect(await screen.findByText('新增 · B-only.py')).toBeTruthy();
  const { act } = await import('@testing-library/react');
  await act(async () => oldPage({ changes: [{ path: 'old-A.py', kind: 'added', portable: true }], total: 2, next_offset: null }));
  expect(screen.getByText('新增 · B-only.py')).toBeTruthy(); expect(screen.queryByText(/old-A.py/)).toBeNull();
});
