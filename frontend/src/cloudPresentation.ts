import { ApiError, errorText } from './api';
import type { CloudBinding, CloudMessage, CloudLive, CloudTask } from './cloudTypes';
import type { MessageRow, ToolState } from './types';
const errors: Record<string, string> = {
  result_capacity: '此任务的结果额度已满，请保存所需结果并处理旧任务', result_busy: '结果正在保存，请等待完成后操作',
  result_revision_conflict: '结果状态已更新，请刷新后再操作', result_output_conflict: '输入文件占用了专用产物目录，请调整产物目录配置或输入范围',
  result_capture_unconfirmed: '上次捕获未形成完整结果，请显式重试保存或放弃本轮结果', result_not_available: '结果尚未保存完整',
  result_patch_budget: '代码补丁超过配置的大小上限', result_byte_budget: '结果总大小超过配置的上限',
  result_file_budget: '文件数量或大小超过配置的上限', result_artifact_budget: '本轮产物数量超过配置的上限',
  artifact_budget: '云端产物存储额度已满，请检查任务产物与存储配置',
  cloud_connection_failed: '云端连接不可达，任务状态尚未确认', cloud_profile_not_found: '找不到配置的云端连接',
  cloud_project_trust_required: '请先授权当前本地项目', cloud_local_source_required: '需要本地项目作为代码来源',
  input_git_required: '当前项目不是 Git 仓库，请改用明确的相对路径', git_repository_required: '当前项目不是 Git 仓库，请改用相对路径',
  environment_capacity: '运行环境额度已满，请先释放不再运行的环境', environment_retained_capacity: '保留任务额度已满，请先处理旧任务',
  cloud_draft_conflict: '草稿已在其他页面更新，本页内容已保留，请重新核对', cloud_binding_conflict: '任务状态已更新，请刷新后再操作',
  task_revision_conflict: '云端状态已变化，请刷新后再操作', cloud_command_unknown: '原操作结果未确认，请核对原操作',
  model_unavailable: '云端模型尚未就绪，请检查该连接的模型配置', runner_not_ready: '任务仍在运行、准备或保存，请等待状态更新',
  cloud_task_not_quiescent: '请先停止本轮并等待必要保存完成', task_delete_requires_stop: '请先停止本轮并等待必要保存，再重新预览删除',
  task_delete_confirmation_changed: '删除预览已过期，请重新预览', task_not_available: '任务已进入删除流程，当前不可操作',
  cloud_task_unavailable: '任务已删除或尚未完成准备', resource_disk_capacity: '云端磁盘空间不足，已阻止新增分配',
  resource_disk_unknown: '云端磁盘状态未确认，已阻止新增分配', sandbox_create_unconfirmed: '创建是否结束尚未确认，任务继续保留占额',
};
export function cloudError(error: unknown) {
  const code = error && typeof error === 'object' && 'code' in error && typeof error.code === 'string' ? error.code : undefined;
  return code ? errors[code] ?? errorText(error instanceof ApiError ? error : new ApiError(code)) : errorText(error);
}
export function taskLabel(binding: CloudBinding) {
  const t = binding.cloud;
  if (t?.active_run_id) return '运行中';
  if (t?.lifecycle === 'deleted') return '已删除';
  if (t?.lifecycle === 'deleting') return '正在删除';
  if (t?.lifecycle === 'reconciling' || t?.reclaim_blockers?.some(v => v.includes('unknown'))) return '需核对';
  if (binding.preparation !== 'prepared') return ({ failed: '准备失败', unknown: '准备结果未确认', cancelled: '已取消' } as Record<string, string>)[binding.preparation] ?? '正在准备';
  if (t?.runner_phase === 'waiting_model') return '等待模型配置';
  if (t?.environment_phase === 'absent') return '环境已释放';
  if (t?.reclaim_blockers.includes('result_pending')) return '正在保存结果';
  return t?.runner_phase === 'ready' ? '可发送消息' : '等待执行端连接';
}
export function canSend(task: CloudTask | null) {
  return !!task && task.lifecycle === 'ready' && task.runner_phase === 'ready' && !task.active_run_id &&
    !task.control_operation_id && !task.environment_operation_id && !task.artifact_operation_id &&
    !task.reclaim_blockers.some(v => v !== 'background_commands');
}
export function messageRow(row: CloudMessage): MessageRow {
  return { ...row, provisional: row.provisional ?? false, state: row.state ?? 'complete',
    detail: row.detail ? { sequence: row.sequence ?? 0, revision: row.detail.revision } : undefined };
}
export function liveTools(live?: CloudLive): Record<string, ToolState> {
  return Object.fromEntries((live?.tools ?? []).map(item => {
    const type = (item as unknown as { event_type?: string }).event_type;
    return [item.tool_call_id, { ...item, state: item.state ?? (type?.endsWith('finished') ? 'finished' : 'running') }];
  }));
}
