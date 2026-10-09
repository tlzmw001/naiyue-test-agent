import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ApprovalPanel from './ApprovalPanel';

afterEach(cleanup);
const requests = [{ tool_call_id: 'one', tool_name: 'read', target: '/outside/one' },
  { tool_call_id: 'two', tool_name: 'read', target: '/outside/two' }];
it('requires an explicit decision for every call and submits one ordered batch', () => {
  const submit = vi.fn();
  render(<ApprovalPanel origin="r" requests={requests} disabled={false} submit={submit} />);
  const button = screen.getByRole('button', { name: '提交审批并继续' });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('radio', { name: '批准 1' }));
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('radio', { name: '拒绝 2' }));
  fireEvent.click(button);
  expect(submit).toHaveBeenCalledWith([{ tool_call_id: 'one', decision: 'approve' }, { tool_call_id: 'two', decision: 'reject' }]);
});
it('keeps choices when a failed submission leaves the same batch in place', () => {
  const { rerender } = render(<ApprovalPanel origin="r" requests={requests} disabled={false} submit={() => {}} />);
  fireEvent.click(screen.getByRole('radio', { name: '批准 1' }));
  rerender(<ApprovalPanel origin="r" requests={requests} disabled={true} submit={() => {}} />);
  rerender(<ApprovalPanel origin="r" requests={requests} disabled={false} submit={() => {}} />);
  expect((screen.getByRole('radio', { name: '批准 1' }) as HTMLInputElement).checked).toBe(true);
});

it('does not treat inherited object names as approval choices', () => {
  render(<ApprovalPanel origin="r" requests={[{ tool_call_id: 'toString', tool_name: 'read', target: '/outside' }]}
    disabled={false} submit={() => {}} />);
  expect((screen.getByRole('button', { name: '提交审批并继续' }) as HTMLButtonElement).disabled).toBe(true);
});
