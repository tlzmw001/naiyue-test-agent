import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ApprovalPanel from './ApprovalPanel';

afterEach(cleanup);
const requests = [{ approval_id: 'one', tool_call_id: 'one', tool_name: 'read', target: '/outside/one' },
  { approval_id: 'two', tool_call_id: 'two', tool_name: 'read', target: '/outside/two' }];
const batch = { approval_batch_id: 'batch', kind: 'paused_root' as const, agent_path: 'root', agent: null, origin_run_id: 'r', items: requests };
it('requires an explicit decision for every call and submits one ordered batch', () => {
  const submit = vi.fn();
  render(<ApprovalPanel batch={batch} disabled={false} submit={submit} />);
  const button = screen.getByRole('button', { name: '提交审批并继续' });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('radio', { name: '批准 1' }));
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('radio', { name: '拒绝 2' }));
  fireEvent.click(button);
  expect(submit).toHaveBeenCalledWith([{ approval_id: 'one', decision: 'approve' }, { approval_id: 'two', decision: 'reject' }], []);
});
it('keeps choices when a failed submission leaves the same batch in place', () => {
  const { rerender } = render(<ApprovalPanel batch={batch} disabled={false} submit={() => {}} />);
  fireEvent.click(screen.getByRole('radio', { name: '批准 1' }));
  rerender(<ApprovalPanel batch={batch} disabled={true} submit={() => {}} />);
  rerender(<ApprovalPanel batch={batch} disabled={false} submit={() => {}} />);
  expect((screen.getByRole('radio', { name: '批准 1' }) as HTMLInputElement).checked).toBe(true);
});

it('does not treat inherited object names as approval choices', () => {
  render(<ApprovalPanel batch={{ ...batch, items: [{ approval_id: 'toString', tool_call_id: 'toString', tool_name: 'read', target: '/outside' }] }}
    disabled={false} submit={() => {}} />);
  expect((screen.getByRole('button', { name: '提交审批并继续' }) as HTMLButtonElement).disabled).toBe(true);
});

it('remembers only approved rows and keeps simultaneous batches independent', () => {
  const submit = vi.fn();
  render(<><ApprovalPanel batch={batch} disabled={false} submit={submit} />
    <ApprovalPanel batch={{ ...batch, approval_batch_id: 'other', agent: 'tester', kind: 'live_child' }}
      disabled={false} submit={() => {}} /></>);
  const approve = screen.getAllByRole('radio', { name: '批准 1' });
  fireEvent.click(approve[0]);
  expect((approve[1] as HTMLInputElement).checked).toBe(false);
  fireEvent.click(screen.getAllByRole('checkbox', { name: '本会话不再询问 1' })[0]);
  fireEvent.click(screen.getAllByRole('radio', { name: '拒绝 2' })[0]);
  fireEvent.click(screen.getAllByRole('button', { name: '提交审批并继续' })[0]);
  expect(submit).toHaveBeenCalledWith([{ approval_id: 'one', decision: 'approve' },
    { approval_id: 'two', decision: 'reject' }], ['one']);
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
