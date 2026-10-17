import type { ContextView } from './contextTypes';
import { decodeSSE, responseBytes } from './sse';
import type { SseFrame } from './sse';
import type { ApprovalBatch, PermissionMode, RunView, SessionControlOptions, SessionRow, SessionView, SkillCatalogView, SubagentDetail, SubagentRecord, ThinkingOptions, ToolDecision } from './types';

export class ApiError extends Error {
  constructor(public code: string) { super(code); }
}

export function browserToken(): string {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const token = fragment.get('access_token');
  if (token) {
    sessionStorage.setItem('naiyue.local-access', token);
    history.replaceState(null, '', window.location.pathname + window.location.search);
  }
  return sessionStorage.getItem('naiyue.local-access') ?? '';
}

export class WebApi {
  constructor(private token: string) {}

  private async response(path: string, init: RequestInit = {}) {
    const response = await fetch('/api' + path, { ...init, headers: {
      Authorization: 'Bearer ' + this.token, ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    } });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new ApiError(body?.error?.code ?? 'http_failed');
    }
    return response;
  }

  private async json<T>(path: string, method = 'GET', body?: object): Promise<T> {
    return (await this.response(path, { method, ...(body ? { body: JSON.stringify(body) } : {}) })).json();
  }

  models() { return this.json<{ default_model_id: string; model_ids: string[]; thinking_options?: Record<string, ThinkingOptions> }>('/models'); }
  selectThinking(id: string, model_id: string, thinking_level: string) {
    return this.json<{ model_id: string; thinking_level: string }>(`/sessions/${encodeURIComponent(id)}/thinking`, 'PATCH', { model_id, thinking_level });
  }
  compact(id: string, request_id = crypto.randomUUID()) {
    return this.json<{ run_id: string; session_id: string }>(`/sessions/${encodeURIComponent(id)}/compact`, 'POST', { request_id });
  }
  sessionControlOptions() { return this.json<SessionControlOptions>('/session-controls'); }
  skills() { return this.json<SkillCatalogView>('/skills'); }
  selectTools(id: string, active_tools: string[]) {
    return this.json<{ active_tools: string[] }>(`/sessions/${encodeURIComponent(id)}/tools`, 'PATCH', { active_tools });
  }
  selectPermissionMode(id: string, permission_mode: PermissionMode) {
    return this.json<{ permission_mode: PermissionMode }>(`/sessions/${encodeURIComponent(id)}/permission-mode`, 'PATCH', { permission_mode });
  }
  sessions() { return this.json<SessionRow[]>('/sessions'); }
  updateSession(id: string, change: { title?: string; pinned?: boolean }) {
    return this.json<SessionRow>(`/sessions/${encodeURIComponent(id)}/presentation`, 'PATCH', change);
  }
  reorderSessions(session_ids: string[]) { return this.json<SessionRow[]>('/session-order', 'PUT', { session_ids }); }
  deleteSession(id: string) { return this.json<{ session_id: string }>(`/sessions/${encodeURIComponent(id)}`, 'DELETE'); }
  create(model_id: string) { return this.json<{ session_id: string }>('/sessions', 'POST', { model_id }); }
  session(id: string) { return this.json<SessionView>(`/sessions/${encodeURIComponent(id)}`); }
  context(id: string) { return this.json<ContextView>(`/sessions/${encodeURIComponent(id)}/context`); }
  subagents(id: string) { return this.json<SubagentRecord[]>(`/sessions/${encodeURIComponent(id)}/subagents`); }
  subagent(id: string, agentId: string) {
    return this.json<SubagentDetail>(`/sessions/${encodeURIComponent(id)}/subagents/${encodeURIComponent(agentId)}`);
  }
  recover(id: string, checkpoint_id: string, action: 'continue' | 'restore_approval') {
    return this.json<SessionView>(`/sessions/${encodeURIComponent(id)}/recover`, 'POST', { checkpoint_id, action });
  }
  select(id: string, model_id: string) {
    return this.json<{ model_id: string }>(`/sessions/${encodeURIComponent(id)}/model`, 'PATCH', { model_id });
  }
  start(id: string, text: string, request_id = crypto.randomUUID()) {
    return this.json<{ run_id: string; session_id: string }>(`/sessions/${encodeURIComponent(id)}/runs`,
      'POST', { text, request_id });
  }
  control(id: string, kind: 'steering' | 'follow_up' | 'stop', text?: string, agent_path = 'root') {
    return this.json<{ accepted: boolean; retained: string[] }>(`/runs/${encodeURIComponent(id)}/controls`,
      'POST', { kind, request_id: crypto.randomUUID(), ...(text === undefined ? {} : { text }),
        ...(agent_path === 'root' ? {} : { agent_path }) });
  }
  run(id: string) { return this.json<RunView>(`/runs/${encodeURIComponent(id)}`); }

  approve(id: string, batch: ApprovalBatch, decisions: ToolDecision[], remember_approved: string[] = [], request_id = crypto.randomUUID()) {
    return this.json<{ run_id: string; session_id: string; kind?: 'live_child'; accepted?: boolean }>(`/sessions/${encodeURIComponent(id)}/approvals`,
      'POST', { request_id, origin_run_id: batch.origin_run_id, approval_batch_id: batch.approval_batch_id, decisions, remember_approved });
  }

  stopApproval(id: string, batch: ApprovalBatch, request_id = crypto.randomUUID()) {
    return this.json<{ run_id: string; session_id: string }>(`/sessions/${encodeURIComponent(id)}/approvals/stop`,
      'POST', { request_id, origin_run_id: batch.origin_run_id, approval_batch_id: batch.approval_batch_id });
  }

  async events(id: string, after: number, signal: AbortSignal, receive: (frame: SseFrame) => void) {
    const response = await this.response(`/runs/${encodeURIComponent(id)}/events?after=${after}`, { signal });
    if (!response.body) throw new ApiError('stream_unavailable');
    for await (const frame of decodeSSE(responseBytes(response.body))) receive(frame);
  }
}

export function errorText(error: unknown): string {
  const code = error instanceof ApiError ? error.code : error instanceof Error ? error.message : '';
  const messages: Record<string, string> = {
    unauthorized: '访问链接已失效，请从启动终端重新打开。',
    invalid_host: '请使用启动时提供的本地地址。',
    invalid_origin: '请求来源不匹配，请从本地页面操作。',
    session_busy: '上一轮或另一个会话仍在运行，输入内容已保留。',
    stale_run: '这次运行已结束，请刷新状态后操作。',
    stale_approval: '审批批次已变化，请重新同步后核对当前调用。',
    session_recovery_blocked: '该会话尚不能安全恢复，请先核对之前的执行状态。',
    checkpoint_mismatch: '已保存状态发生变化，请核对刷新后的历史再操作。',
    session_save_unconfirmed: '恢复保存未确认，请核对当前历史后再操作。',
    session_delete_unconfirmed: '删除未确认，会话内容已保留，请刷新核对后重试。',
    stale_session_order: '会话列表已变化，请核对刷新后的列表再排序。',
    invalid_session_order: '请在同一组内调整会话顺序。',
    approval_restore_unavailable: '原审批的目标或权限条件已变化。可核对后重试，或结束旧批次后重新提出任务。',
    manual_review_required: '执行结果需要人工核对，当前不能继续运行。',
    workspace_mismatch: '该会话属于其他工作区，请用对应工作区启动后恢复。',
    resync_required: '事件连接需要重新同步，已保留当前内容。',
    sequence_gap: '事件交付不完整，请重新同步。',
    request_window_full: '当前服务的请求保留窗口已满，请等待运行结束后重启服务。',
    view_too_large: '当前可显示内容超出交付上限。',
    invalid_request: '输入格式无效或内容过长，请检查后重新提交。',
    invalid_tool_selection: '工具选择无效，请刷新可用工具后重试。',
    permission_mode_unavailable: '该权限模式不可用，请检查 Auto 审查模型配置或刷新后重试。',
    context_view_unavailable: '上下文读取失败，可以重试；当前任务不会受到影响。',
    skill_catalog_unavailable: '技能列表读取失败，可以重试；输入内容已保留。',
    thinking_unavailable: '该推理档位不可用，或模型选择已变化，请核对后重试。',
    subagent_not_found: '该子智能体记录不在当前会话中，请返回列表重新选择。',
    subagent_history_unavailable: '子智能体记录读取失败，可以重试；当前任务不会因此重跑。',
  };
  return messages[code] ?? '操作未确认，内容已保留。请检查连接或重新同步。';
}
