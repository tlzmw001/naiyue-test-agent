import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ApprovalPanel from './ApprovalPanel';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const requests = [{ approval_id: 'one', tool_call_id: 'one', tool_name: 'read', target: '/outside/one' },
  { approval_id: 'two', tool_call_id: 'two', tool_name: 'read', target: '/outside/two' }];
const batch = { approval_batch_id: 'batch', kind: 'paused_root' as const, agent_path: 'root', agent: null, origin_run_id: 'r', items: requests };
it('labels MCP tool-wide session trust explicitly and submits the same row identity', () => {
  const submit = vi.fn();
  render(<ApprovalPanel batch={{ ...batch, items: [{ ...requests[0], tool_name: 'mcp__local__echo', remember_scope: 'tool' }] }}
    disabled={false} submit={submit} />);
  fireEvent.click(screen.getByRole('button', { name: '本会话始终允许此工具 1' }));
  expect(submit).toHaveBeenCalledExactlyOnceWith([{ approval_id: 'one', decision: 'approve' }], ['one']);
});
it('requires an explicit decision for every call and submits one ordered batch', () => {
  const submit = vi.fn();
  render(<ApprovalPanel batch={batch} disabled={false} submit={submit} />);
  const button = screen.getByRole('button', { name: '提交审批并继续' });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '批准 1' }));
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '拒绝 2' }));
  fireEvent.click(button);
  expect(submit).toHaveBeenCalledWith([{ approval_id: 'one', decision: 'approve' }, { approval_id: 'two', decision: 'reject' }], []);
});
it('keeps choices when a failed submission leaves the same batch in place', () => {
  const { rerender } = render(<ApprovalPanel batch={batch} disabled={false} submit={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: '批准 1' }));
  rerender(<ApprovalPanel batch={batch} disabled={true} submit={() => {}} />);
  rerender(<ApprovalPanel batch={batch} disabled={false} submit={() => {}} />);
  expect(screen.getByRole('button', { name: '批准 1' }).getAttribute('aria-pressed')).toBe('true');
});

it('does not treat inherited object names as approval choices', () => {
  const submit = vi.fn();
  render(<ApprovalPanel batch={{ ...batch, items: [{ approval_id: 'toString', tool_call_id: 'toString', tool_name: 'read', target: '/outside' }] }}
    disabled={false} submit={submit} />);
  expect(screen.getAllByRole('button').filter(button => button.hasAttribute('aria-pressed')).every(button => button.getAttribute('aria-pressed') === 'false')).toBe(true);
  expect(submit).not.toHaveBeenCalled();
});

it('remembers only approved rows and keeps simultaneous batches independent', () => {
  const submit = vi.fn();
  render(<><ApprovalPanel batch={batch} disabled={false} submit={submit} />
    <ApprovalPanel batch={{ ...batch, approval_batch_id: 'other', agent: 'tester', kind: 'live_child' }}
      disabled={false} submit={() => {}} /></>);
  const approve = screen.getAllByRole('button', { name: '批准 1' });
  fireEvent.click(approve[0]);
  expect(approve[1].getAttribute('aria-pressed')).toBe('false');
  fireEvent.click(screen.getAllByRole('button', { name: '本会话允许 1' })[0]);
  fireEvent.click(screen.getAllByRole('button', { name: '拒绝 2' })[0]);
  fireEvent.click(screen.getAllByRole('button', { name: '提交审批并继续' })[0]);
  expect(submit).toHaveBeenCalledWith([{ approval_id: 'one', decision: 'approve' },
    { approval_id: 'two', decision: 'reject' }], ['one']);
});

it.each([['批准', 'approve', false], ['本会话允许', 'approve', true], ['拒绝', 'reject', false]] as const)
('submits a single %s choice directly through the same ordered batch contract', (label, decision, remember) => {
  const submit = vi.fn();
  render(<ApprovalPanel batch={{ ...batch, items: [requests[0]] }} disabled={false} submit={submit} />);
  fireEvent.click(screen.getByRole('button', { name: `${label} 1` }));
  expect(submit).toHaveBeenCalledExactlyOnceWith([{ approval_id: 'one', decision }], remember ? ['one'] : []);
});

it('retains a single decision and exposes explicit retry after submission fails', async () => {
  const submit = vi.fn();
  const { rerender } = render(<ApprovalPanel batch={{ ...batch, items: [requests[0]] }} disabled={false} submit={submit} />);
  fireEvent.click(screen.getByRole('button', { name: '本会话允许 1' }));
  rerender(<ApprovalPanel batch={{ ...batch, items: [requests[0]] }} disabled={true} submit={submit} />);
  expect((screen.getByRole('button', { name: '重试提交审批' }) as HTMLButtonElement).disabled).toBe(true);
  rerender(<ApprovalPanel batch={{ ...batch, items: [requests[0]] }} disabled={false} submit={submit} />);
  expect(screen.getByRole('button', { name: '本会话允许 1' }).getAttribute('aria-pressed')).toBe('true');
  await waitFor(() => expect((screen.getByRole('button', { name: '重试提交审批' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '重试提交审批' }));
  expect(submit).toHaveBeenCalledTimes(2);
});

it('stops a batch without creating approve or reject decisions', () => {
  const submit = vi.fn(), stop = vi.fn();
  const { rerender } = render(<ApprovalPanel batch={batch} disabled={false} submit={submit} stop={stop} />);
  fireEvent.click(screen.getByRole('button', { name: '停止本批并结束运行' }));
  expect(stop).toHaveBeenCalledTimes(1);
  expect(submit).not.toHaveBeenCalled();
  rerender(<ApprovalPanel batch={{ ...batch, kind: 'live_child' }} disabled={true} submit={submit} stop={stop} />);
  expect((screen.getByRole('button', { name: '停止该子任务' }) as HTMLButtonElement).disabled).toBe(true);
});

it('does not duplicate a single approval while its submission is pending', async () => {
  let finish!: () => void;
  const submit = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
  render(<ApprovalPanel batch={{ ...batch, items: [requests[0]] }} disabled={false} submit={submit} />);
  fireEvent.click(screen.getByRole('button', { name: '批准 1' }));
  fireEvent.click(screen.getByRole('button', { name: '重试提交审批' }));
  fireEvent.click(screen.getByRole('button', { name: '拒绝 1' }));
  expect(submit).toHaveBeenCalledExactlyOnceWith([{ approval_id: 'one', decision: 'approve' }], []);
  await act(async () => finish());
  expect((screen.getByRole('button', { name: '重试提交审批' }) as HTMLButtonElement).disabled).toBe(false);
});

it('reports a rejected submission, retains the decision and allows explicit retry', async () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  const submit = vi.fn().mockRejectedValueOnce(new Error('PRIVATE_NETWORK_DETAIL')).mockResolvedValue(undefined);
  render(<ApprovalPanel batch={{ ...batch, items: [requests[0]] }} disabled={false} submit={submit} />);
  fireEvent.click(screen.getByRole('button', { name: '本会话允许 1' }));
  expect((await screen.findByRole('alert')).textContent).toBe('提交未确认，请核对会话状态后重试。');
  expect(screen.queryByText('PRIVATE_NETWORK_DETAIL')).toBeNull();
  expect(warn).toHaveBeenCalledExactlyOnceWith('approval_submission_failed');
  expect(screen.getByRole('button', { name: '本会话允许 1' }).getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(screen.getByRole('button', { name: '重试提交审批' }));
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
  expect(submit).toHaveBeenLastCalledWith([{ approval_id: 'one', decision: 'approve' }], ['one']);
  expect(screen.queryByRole('alert')).toBeNull();
});
