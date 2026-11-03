import { expect, it } from 'vitest';
import { canSend, cloudError, taskLabel } from './cloudPresentation';
import type { CloudBinding, CloudTask } from './cloudTypes';

function task(changes: Partial<CloudTask> = {}): CloudTask {
  return { task_id: 'T', project_id: 'P', input_id: 'I', generation: 1, revision: 1,
    lifecycle: 'ready', runner_phase: 'ready', environment_phase: 'backend_ready', sandbox_id: 'container',
    active_run_id: null, last_run_id: 'old', last_error_code: null, last_quiescent_at: null,
    environment_operation_id: null, control_operation_id: null, artifact_operation_id: null,
    runner_model_ids: ['A'], reclaim_blockers: [], ...changes };
}
function binding(value: CloudTask): CloudBinding {
  return { binding_id: 'B', local_session_id: 'S', profile_id: 'work', instance_id: 'instance',
    task_id: 'T', input_id: 'I', revision: 1, preparation: 'prepared', retained: true, create_command_id: 'C', cloud: value };
}

it('allows the next message while a mock service delays a not-yet-started capture', () => {
  const value = task({ reclaim_blockers: ['background_commands', 'result_pending'] });
  expect(canSend(value)).toBe(true);
  expect(taskLabel(binding(value))).toBe('后台服务运行中，可继续发送');
  expect(taskLabel(binding(task({ reclaim_blockers: ['result_pending'] })))).toBe('等待捕获，可继续发送');
});

it.each([
  { task: task({ artifact_operation_id: 'result', reclaim_blockers: ['result_pending', 'result_capture'] }), label: '正在捕获并保存结果' },
  { task: task({ reclaim_blockers: ['result_failed'] }), label: '结果保存失败，需处理' },
  { task: task({ reclaim_blockers: ['result_retry_pending'] }), label: '等待重试保存结果' },
])('does not turn an active or retry-required capture into send permission: $label', value => {
  expect(canSend(value.task)).toBe(false);
  expect(taskLabel(binding(value.task))).toBe(value.label);
});

it.each(['required_history', 'recovery_required', 'unknown', 'result_unknown'])('keeps %s protected even with a pending result', blocker => {
  expect(canSend(task({ reclaim_blockers: ['background_commands', 'result_pending', blocker] }))).toBe(false);
});

it('describes a retention budget without telling the user to discard the workspace', () => {
  const message = cloudError({ code: 'result_capacity' });
  expect(message).toMatch(/保留/);
  expect(message).toMatch(/接收|下载/);
  expect(message).not.toMatch(/旧任务|重建|新建任务/);
});
