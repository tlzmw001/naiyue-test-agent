import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, afterAll, expect, it, vi } from 'vitest';
import CloudResults from './CloudResults';
import { installDialogMock } from '../testDialogs';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding } from '../cloudTypes';
let restoreDialogs: () => void;
beforeAll(() => { restoreDialogs = installDialogMock(); });
afterAll(() => restoreDialogs());
afterEach(() => { cleanup(); sessionStorage.clear(); });
const binding = { binding_id: 'B', revision: 8, cloud: { artifact_operation_id: null } } as CloudBinding;
const file = { role: 'artifact' as const, artifact: { artifact_id: 'F', relative_name: 'report.html', size: 23, sha256: 'a'.repeat(64) } };
function api(phase = 'available') {
  return { results: vi.fn().mockResolvedValue([{ result_id: 'R', source_run_id: 'run12345', phase, revision: 2, outcome: 'failed' }]),
    result: vi.fn().mockResolvedValue({ plan: { result_id: 'R', input_id: 'i'.repeat(64) }, phase, revision: 2, boundary: { outcome: 'failed' }, bundle: { files: [file] } }),
    changes: vi.fn().mockResolvedValue({ changes: [{ path: 'new.py', kind: 'added', portable: true }], total: 1, next_offset: null }),
    resultPreview: vi.fn().mockResolvedValue({ text: '<script>bad()</script>', binary: false, complete: true, next_offset: 23 }),
    resultAction: vi.fn().mockResolvedValue({}) } as unknown as CloudApi;
}
it('shows actual files, separates failed execution from available result and escapes preview', async () => {
  const cloud = api(); const view = render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />); fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  fireEvent.click(await screen.findByRole('button', { name: /运行 run12345/ }));
  expect(await screen.findByText('新增 · new.py')).toBeTruthy();
  expect(screen.getAllByText(/执行失败/).length).toBeGreaterThan(0);
  expect(screen.queryByText(/文件完整性已核对/)).toBeNull();
  expect(screen.getByRole('button', { name: '保存到本地' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '预览' }));
  expect(await screen.findByText('<script>bad()</script>')).toBeTruthy(); expect(view.container.querySelector('script')).toBeNull();
  expect(screen.queryByRole('button', { name: '下载' })).toBeNull(); expect(view.container.querySelector('a[download]')).toBeNull();
});
it('requires explicit confirmation before abandoning and sends fixed result revision', async () => {
  const cloud = api('failed'); render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />); fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  fireEvent.click(await screen.findByRole('button', { name: /运行 run12345/ }));
  fireEvent.click(await screen.findByRole('button', { name: '放弃本轮结果' })); expect(cloud.resultAction).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '确认放弃' }));
  expect(cloud.resultAction).toHaveBeenCalledWith('S', 'B', 'R', expect.objectContaining({ action: 'abandon', expected_revision: 2, expected_binding_revision: 8 }));
});
it('does not dispatch or invent outputs for an empty result list', async () => {
  const cloud = api(); vi.mocked(cloud.results).mockResolvedValue([]); render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />); fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
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
  render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />); fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  fireEvent.click(await screen.findByRole('button', { name: /运行 A1234567/ }));
  fireEvent.click(await screen.findByRole('button', { name: '加载更多代码变化' }));
  fireEvent.click(screen.getByRole('button', { name: /运行 B1234567/ }));
  expect(await screen.findByText('新增 · B-only.py')).toBeTruthy();
  const { act } = await import('@testing-library/react');
  await act(async () => oldPage({ changes: [{ path: 'old-A.py', kind: 'added', portable: true }], total: 2, next_offset: null }));
  expect(screen.getByText('新增 · B-only.py')).toBeTruthy(); expect(screen.queryByText(/old-A.py/)).toBeNull();
});

it('receives selected reports locally before preparing an analysis draft, without sending a chat message', async () => {
  const cloud = api(); Object.assign(cloud, { projectId: 'P' });
  const received = { receive_id: 'C', command_id: 'C', local_project_id: 'P', local_session_id: 'S', binding_id: 'B',
    instance_id: 'I', task_id: 'T', result_id: 'R', input_id: 'i'.repeat(64), phase: 'available', files: [], error_code: null, active: false, created_at: 1, updated_at: 1 };
  const draft = { local_project_id: 'P', local_session_id: 'S', receive_id: 'C', text: '分析任务报告', material_refs: [] };
  const receive = vi.fn().mockImplementation(async (_s, _b, _r, body) => ({ ...received, receive_id: body.command_id, command_id: body.command_id }));
  const analysisDraft = vi.fn().mockImplementation(async (_s, id) => ({ ...draft, receive_id: id }));
  Object.assign(cloud, { receive, analysisDraft, receiveStatus: vi.fn().mockResolvedValue(received) });
  const accept = vi.fn(); render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} onAnalysisDraft={accept} />); fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  fireEvent.click(await screen.findByRole('button', { name: /运行 run12345/ }));
  fireEvent.click(await screen.findByRole('checkbox', { name: /report.html/ }));
  fireEvent.click(screen.getByRole('button', { name: '交给主 Agent 分析' }));
  await waitFor(() => expect(accept).toHaveBeenCalled());
  expect(receive).toHaveBeenCalledWith('S', 'B', 'R', { command_id: expect.any(String), artifact_ids: ['F'], include_code: true });
  expect(analysisDraft).toHaveBeenCalledOnce();
  await waitFor(() => expect(accept).toHaveBeenCalledWith(expect.objectContaining({ text: '分析任务报告' })));
});

it.each(['session', 'result'])('does not deliver a valid late analysis draft after changing the %s', async scope => {
  const cloud = api(); Object.assign(cloud, { projectId: 'P' });
  let resolve!: (value: object) => void;
  vi.mocked(cloud.results).mockResolvedValue(['R', 'B'].map(result_id => ({ result_id, source_run_id: result_id + '-run', phase: 'available',
    revision: 2, outcome: 'completed', error_code: null, captured_at: 1, files_count: 1 })));
  const detail = await cloud.result('S', 'B', 'R');
  vi.mocked(cloud.result).mockClear().mockImplementation(async (_s, _b, rid) => ({ ...detail, plan: { ...detail.plan, result_id: rid } }));
  Object.assign(cloud, { receive: vi.fn().mockImplementation(async (_s, _b, _r, body) => ({ receive_id: body.command_id, command_id: body.command_id,
    local_project_id: 'P', local_session_id: 'S', binding_id: 'B', result_id: 'R', input_id: 'i'.repeat(64), phase: 'available', files: [], active: false })),
    analysisDraft: vi.fn().mockImplementation(() => new Promise(done => { resolve = done; })) });
  const accept = vi.fn(); const view = render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} onAnalysisDraft={accept} />); fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  fireEvent.click(await screen.findByRole('button', { name: '交给主 Agent 分析' }));
  await waitFor(() => expect(cloud.analysisDraft).toHaveBeenCalled());
  const receiveId = vi.mocked(cloud.analysisDraft).mock.calls[0][1];
  if (scope === 'session') {
    view.rerender(<CloudResults api={cloud} sid="other" binding={binding} active changed={vi.fn()} onAnalysisDraft={accept} />);
    fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  } else {
    fireEvent.click(screen.getByRole('button', { name: /运行 B-run/ }));
    await screen.findByText(/结果 B/);
  }
  await act(async () => resolve({ local_project_id: 'P', local_session_id: 'S', receive_id: receiveId, text: 'old response', material_refs: [] }));
  expect(accept).not.toHaveBeenCalled();
});

it('shows the run which superseded an uncaptured result without inventing failed execution or files', async () => {
  const cloud = api('superseded');
  vi.mocked(cloud.results).mockResolvedValue([{ result_id: 'R', source_run_id: 'run12345', phase: 'superseded', revision: 3,
    outcome: 'completed', error_code: null, captured_at: null, files_count: null, superseded_by: 'next87654321' }]);
  vi.mocked(cloud.result).mockResolvedValue({ plan: { result_id: 'R', source_run_id: 'run12345', input_id: 'i'.repeat(64) },
    phase: 'superseded', revision: 3, error_code: null, boundary: { outcome: 'completed' }, bundle: null, superseded_by: 'next87654321' });
  render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />); fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  fireEvent.click(screen.getByRole('button', { name: '历史记录' }));
  fireEvent.click(await screen.findByRole('button', { name: /运行 run12345 · 已由后续运行替代/ }));
  expect(await screen.findByText(/未生成本轮固定文件/)).toBeTruthy();
  expect(screen.getAllByText(/next8765/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/执行已结束/).length).toBeGreaterThan(0);
  expect(screen.queryByText(/执行失败/)).toBeNull();
  expect(screen.queryByRole('button', { name: '接收到本机' })).toBeNull();
  expect(screen.queryByRole('button', { name: '下载' })).toBeNull();
  expect(cloud.changes).not.toHaveBeenCalled(); expect(cloud.resultAction).not.toHaveBeenCalled();
});

it.each([
  { phase: 'deleting', label: '正在清理结果文件', detail: /暂不可下载或接收/ },
  { phase: 'deleted', label: '结果文件已清理', detail: /执行结论仍保留/ },
])('does not offer stale bundle files when the result is $phase', async value => {
  const cloud = api(value.phase);
  render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />); fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  if (value.phase === 'deleted') fireEvent.click(screen.getByRole('button', { name: '历史记录' }));
  fireEvent.click(await screen.findByRole('button', { name: new RegExp('运行 run12345 · ' + value.label) }));
  expect(await screen.findByText(value.detail)).toBeTruthy();
  expect(screen.getAllByText(/执行失败/).length).toBeGreaterThan(0);
  expect(screen.queryByRole('button', { name: '接收到本机' })).toBeNull();
  expect(screen.queryByRole('button', { name: '预览' })).toBeNull();
  expect(screen.queryByRole('button', { name: '下载' })).toBeNull();
  expect(cloud.changes).not.toHaveBeenCalled();
});
it('defaults to the latest retrievable result, keeps capture failures visible, and only loads history pages on demand', async () => {
  const cloud = api();
  const row = (id: string, phase: string, files_count: number | null) => ({
    result_id: id, source_run_id: id + '-run', phase, files_count, revision: 2, outcome: 'completed',
    error_code: null, captured_at: null,
  });
  vi.mocked(cloud.results).mockImplementation(async (_sid, _bid, offset) => offset === 20 ? [row('old', 'deleted', null)] :
    [row('empty', 'available', 0), row('failed', 'failed', null), row('R', 'available', 1),
      ...Array.from({ length: 17 }, (_, i) => row('deleted' + i, 'deleted', null))]);
  render(<CloudResults api={cloud} sid="S" binding={{ ...binding, input_id: null }} active changed={vi.fn()} />);
  expect(cloud.results).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  await waitFor(() => expect(screen.getByRole('button', { name: /运行 R-run/ }).getAttribute('aria-pressed')).toBe('true'));
  expect(screen.getByRole('button', { name: /运行 failed/ })).toBeTruthy();
  expect(screen.queryByRole('button', { name: /运行 empty/ })).toBeNull();
  expect(cloud.results).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: '历史记录' }));
  expect(await screen.findByRole('button', { name: /运行 empty.*无产物/ })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '加载更早记录' }));
  expect(await screen.findByRole('button', { name: /运行 old-run/ })).toBeTruthy();
  expect(cloud.results).toHaveBeenLastCalledWith('S', 'B', 20, expect.any(AbortSignal));
});
it('finds a still-retained result behind a full page of history without fetching history pages', async () => {
  const cloud = api();
  vi.mocked(cloud.results).mockResolvedValue(Array.from({ length: 20 }, (_, index) => ({
    result_id: 'old-' + index, source_run_id: 'old-' + index, phase: 'deleted', files_count: null,
    revision: 2, outcome: 'completed', error_code: null, captured_at: null,
  })));
  vi.mocked(cloud.result).mockResolvedValue({ plan: { result_id: 'R', source_run_id: 'retained-run', input_id: null }, phase: 'available',
    revision: 2, error_code: null, boundary: { outcome: 'completed' }, bundle: { captured_at: 1, files: [file] } });
  const cloudBinding = { ...binding, input_id: null, cloud: { ...binding.cloud, result_ids: ['R'] } } as CloudBinding;
  render(<CloudResults api={cloud} sid="S" binding={cloudBinding} active changed={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  expect(await screen.findByRole('button', { name: /运行 retained/ })).toBeTruthy();
  expect(await screen.findByRole('checkbox', { name: /report.html/ })).toBeTruthy();
  expect(cloud.results).toHaveBeenCalledOnce();
});
it('selects the latest available result on reopening after the previous one was cleaned up', async () => {
  const cloud = api();
  const row = (id: string, phase: string) => ({ result_id: id, source_run_id: id + '-run', phase,
    revision: 2, outcome: 'completed', error_code: null, captured_at: 1, files_count: phase === 'available' ? 1 : null });
  vi.mocked(cloud.results).mockResolvedValue([row('A', 'available')]);
  const detail = await cloud.result('S', 'B', 'A');
  vi.mocked(cloud.result).mockClear().mockImplementation(async (_s, _b, rid) => ({ ...detail, plan: { ...detail.plan, result_id: rid, input_id: null } }));
  render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  await screen.findByText('结果 A');
  fireEvent.click(screen.getByRole('button', { name: '关闭结果窗口' }));
  vi.mocked(cloud.results).mockResolvedValue([row('B', 'available'), row('A', 'deleted')]);
  fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  expect(await screen.findByText('结果 B')).toBeTruthy();
  expect(screen.getByRole('button', { name: /运行 B-run/ }).getAttribute('aria-pressed')).toBe('true');
});
it('ignores a late browser download ticket even after returning to the same result', async () => {
  const cloud = api();
  let finish!: (value: { url: string; expires_in: number }) => void;
  Object.assign(cloud, { downloadLink: vi.fn().mockImplementation(() => new Promise(resolve => { finish = resolve; })) });
  vi.mocked(cloud.results).mockResolvedValue(['A', 'B'].map(result_id => ({ result_id, source_run_id: result_id + '-run', phase: 'available',
    revision: 2, outcome: 'completed', error_code: null, captured_at: 1, files_count: 1 })));
  vi.mocked(cloud.result).mockImplementation(async (_s, _b, rid) => ({ plan: { result_id: rid, source_run_id: rid, input_id: null }, phase: 'available',
    revision: 2, error_code: null, boundary: { outcome: 'completed' }, bundle: { captured_at: 1, files: [file] } }));
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  try {
    render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
    fireEvent.click(await screen.findByRole('button', { name: '浏览器下载' }));
    fireEvent.click(screen.getByRole('button', { name: /运行 B-run/ }));
    await screen.findByText('结果 B');
    fireEvent.click(screen.getByRole('button', { name: /运行 A-run/ }));
    await screen.findByText('结果 A');
    await act(async () => finish({ url: '/api/client/v1/cloud-downloads/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa', expires_in: 300 }));
    expect(click).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '浏览器下载' })).not.toHaveProperty('disabled', true);
  } finally { click.mockRestore(); }
});
it('keeps the original local save status reachable after the cloud result is cleaned up', async () => {
  const cloud = api('deleted');
  const fixed = { action: 'files', command_id: 'save-1', receive_id: 'receive-1', destination_handle: 'P', destination_path: '/reports', name: '' };
  const deliveryStatus = vi.fn().mockResolvedValue({ ...fixed, local_project_id: 'P', local_session_id: 'S',
    phase: 'completed', conflicts: [], changes: [], error_code: null });
  Object.assign(cloud, { projectId: 'P', deliveryStatus, executeDelivery: vi.fn() });
  sessionStorage.setItem('naiyue.cloud-files.P.S.B.R', JSON.stringify(fixed));
  render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  fireEvent.click(screen.getByRole('button', { name: '历史记录' }));
  fireEvent.click(await screen.findByRole('button', { name: /运行 run12345 · 结果文件已清理/ }));
  expect(await screen.findByText('文件已保存到所选文件夹')).toBeTruthy();
  expect(deliveryStatus).toHaveBeenCalledExactlyOnceWith('S', 'save-1', expect.any(AbortSignal));
  expect(cloud.executeDelivery).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: '保存到本地' })).toBeNull();
});
it('opens results in a centered dialog and returns focus when closed', async () => {
  const restoreDialog = installDialogMock();
  try {
    render(<CloudResults api={api()} sid="S" binding={binding} active changed={vi.fn()} />);
    const open = screen.getByRole('button', { name: /结果与产物/ }); fireEvent.click(open);
    expect(await screen.findByRole('dialog', { name: /结果与产物/ })).toHaveProperty('open', true);
    fireEvent.click(screen.getByRole('button', { name: '关闭结果窗口' }));
    expect(screen.queryByRole('dialog')).toBeNull(); expect(document.activeElement).toBe(open);
  } finally {
    restoreDialog();
  }
});
it('handles Escape in the result dialog without closing its surrounding sidebar', async () => {
  const sidebar = vi.fn((event: KeyboardEvent) => { if (!event.defaultPrevented && event.key === 'Escape') sidebarClosed(); });
  const sidebarClosed = vi.fn(); window.addEventListener('keydown', sidebar);
  try {
    render(<CloudResults api={api()} sid="S" binding={binding} active changed={vi.fn()} />);
    const open = screen.getByRole('button', { name: /结果与产物/ }); fireEvent.click(open);
    fireEvent.keyDown(await screen.findByRole('dialog'), { key: 'Escape' });
    expect(sidebarClosed).not.toHaveBeenCalled(); expect(screen.queryByRole('dialog')).toBeNull(); expect(document.activeElement).toBe(open);
  } finally { window.removeEventListener('keydown', sidebar); }
});

it('renders an output-only task without reading or formatting a Git baseline', async () => {
  const cloud = api();
  vi.mocked(cloud.result).mockResolvedValue({ plan: { result_id: 'R', source_run_id: 'run12345', input_id: null }, phase: 'available', revision: 2, error_code: null, boundary: { outcome: 'completed' }, bundle: { captured_at: 1, files: [] } });
  render(<CloudResults api={cloud} sid="S" binding={binding} active changed={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  fireEvent.click(await screen.findByRole('button', { name: /运行 run12345/ }));
  expect(await screen.findByText('结果 R')).toBeTruthy();
  expect(cloud.changes).not.toHaveBeenCalled();
  expect(screen.queryByText(/代码变化/)).toBeNull();
});
