import { ApiError, errorText } from './api';
import type { CloudBinding, CloudMessage, CloudLive, CloudTask } from './cloudTypes';
import type { MessageRow, ToolState } from './types';
const errors: Record<string, string> = {
  cloud_result_identity_mismatch: '结果与当前任务不一致，已停止展示，请刷新当前任务核对',
  cloud_delivery_preflight_changed: '预检后本机文件已变化，已停止本次写入，请重新预检',
  cloud_delivery_baseline_changed: '应用基线已由另一项操作更新，请重新预检当前结果',
  cloud_delivery_destination_busy: '目标目录有另一项取回正在写入，请等待其完成后核对',
  cloud_delivery_path_unsafe: '目标路径不是可替换的普通文件或安全链接，请处理路径后重新预检',
  cloud_delivery_name_invalid: '请填写有效的新目录名；目录名不能包含路径分隔符', cloud_delivery_destination_forbidden: '交付目标不属于当前已授权项目',
  cloud_delivery_not_found: '尚未找到原交付回执，请使用原操作核对预检', cloud_delivery_request_conflict: '原交付参数已固定，请核对同一操作',
  cloud_delivery_receive_unavailable: '本机接收记录尚不可用，请先核对材料', cloud_delivery_not_prepared: '代码交付尚未完成有效预检，请先核对原预检',
  cloud_delivery_capacity: '本机交付额度或磁盘预算已满，请等待正在进行的操作完成', cloud_delivery_project_busy: '当前项目有另一项交付正在写入，请稍后查询',
  cloud_delivery_root_changed: '授权项目目录身份已变化，已停止交付，请重新核对项目', cloud_delivery_conflict: '当前文件与固定基线冲突，已停止写入',
  cloud_delivery_destination_exists: '目标目录已存在，请使用另一个新目录名', cloud_delivery_branch_source_required: '缺少原 Git 来源回执，当前结果不能创建本地分支',
  cloud_delivery_interrupted: '上次交付状态尚未确认，请查询原操作并显式继续', cloud_delivery_failed: '代码交付未完成，请核对原操作和冲突信息',
  cloud_delivery_patch_invalid: '代码补丁验证失败，未授权写入项目', cloud_delivery_output_mismatch: '补丁结果与固定输出清单不一致，已拒绝交付',
  cloud_delivery_identity_mismatch: '交付回执或材料身份与当前会话不一致', cloud_delivery_input_changed: '原输入文件发生变化，请核对固定输入',
  cloud_delivery_postcheck_failed: '写入后核对未完成，请保留原操作并检查当前文件', cloud_delivery_export_unknown: '导出是否完成尚未确认，请查询原操作',
  cloud_receive_not_found: '尚未找到原接收回执，请继续原接收以核对同一请求', cloud_receive_conflict: '原接收身份已固定，请核对原请求',
  cloud_receive_capacity: '本机接收额度已满。可先清理未引用缓存；仍被引用的材料会保留，正在接收时请等待完成后重试。', cloud_receive_selection_invalid: '所选报告不属于此固定结果，请重新核对',
  cloud_receive_busy: '本机材料正在接收、交付、读取或清理，请完成后重试。',
  cloud_delivery_path_unsupported: '结果包含本机不支持的文件名，请在云端重命名列出的文件后重新生成结果；不能强制覆盖。',
  cloud_receive_identity_mismatch: '接收回执与当前会话或结果不一致，已保留原输入', cloud_receive_input_mismatch: '接收结果的输入身份不一致',
  cloud_receive_manifest_mismatch: '结果清单校验失败，请核对原结果', cloud_receive_source_changed: '本机接收文件发生变化，请继续原接收核对',
  cloud_receive_offset_unconfirmed: '本机接收进度尚未确认，请继续原接收核对', cloud_receive_size_mismatch: '接收字节长度与固定结果不一致',
  cloud_material_not_found: '找不到已接收材料，请重新核对本机接收记录', cloud_material_unavailable: '本机材料缺失或校验未通过，请先完成接收',
  cloud_material_identity_mismatch: '材料不属于当前项目或会话，已拒绝使用', cloud_material_budget: '本机材料数量或大小超过配置上限',
  cloud_materials_unavailable: '当前会话的材料读取能力尚未就绪，请重新核对',
  result_capacity: '当前保留的结果及接收、下载保护已占满额度，请等待操作完成或处理仍被引用的结果', result_busy: '结果正在保存，请等待完成后操作',
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
  const code = error && typeof error === 'object' && 'code' in error && typeof error.code === 'string' ? error.code : error instanceof Error && errors[error.message] ? error.message : undefined;
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
  if (t?.artifact_operation_id || t?.reclaim_blockers.includes('result_capture')) return '正在捕获并保存结果';
  if (t?.reclaim_blockers.includes('result_failed')) return '结果保存失败，需处理';
  if (t?.reclaim_blockers.includes('result_retry_pending')) return '等待重试保存结果';
  if (t?.reclaim_blockers.includes('result_pending')) return t.reclaim_blockers.includes('background_commands') ? '后台服务运行中，可继续发送' : '等待捕获，可继续发送';
  return t?.runner_phase === 'ready' ? '可发送消息' : '等待执行端连接';
}
export function canSend(task: CloudTask | null) {
  return !!task && task.lifecycle === 'ready' && task.runner_phase === 'ready' && !task.active_run_id &&
    !task.control_operation_id && !task.environment_operation_id && !task.artifact_operation_id &&
    !task.reclaim_blockers.some(v => v !== 'background_commands' && v !== 'result_pending');
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

export function cloudTime(value?: number | null) { return value === undefined || value === null ? '' : new Date(value * 1000).toLocaleString(); }
