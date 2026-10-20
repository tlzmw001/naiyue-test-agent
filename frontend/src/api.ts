import type { ContextView } from './contextTypes';
import type { McpRefreshResult } from './types';
import type { SessionUsageView } from './usageTypes';
import { decodeSSE, responseBytes } from './sse';
import { textContent } from './agentEvents';
import type { SseFrame } from './sse';
import type { ApprovalBatch, FileChangeView, HistoryPage, MessageRow, PermissionMode, RunView, SessionControlOptions, SessionRow, SessionView, SkillCatalogView, SubagentDetail, SubagentRecord, ThinkingOptions, ToolDecision } from './types';

export interface ServiceInfo {
  workspace_root: string | null;
  workspace_label: string | null;
}

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

  private async json<T>(path: string, method = 'GET', body?: object, signal?: AbortSignal): Promise<T> {
    return (await this.response(path, { method, ...(body ? { body: JSON.stringify(body) } : {}), ...(signal ? { signal } : {}) })).json();
  }

  models() { return this.json<{ default_model_id: string; model_ids: string[]; thinking_options?: Record<string, ThinkingOptions> }>('/models'); }
  serviceInfo(signal?: AbortSignal) { return this.json<ServiceInfo>('/service', 'GET', undefined, signal); }
  selectThinking(id: string, model_id: string, thinking_level: string) {
    return this.json<{ model_id: string; thinking_level: string }>(`/sessions/${encodeURIComponent(id)}/thinking`, 'PATCH', { model_id, thinking_level });
  }
  regenerate(id: string, expected_reply_id: string, expected_conversation_revision: number, request_id = crypto.randomUUID()) {
    return this.json<{ run_id?: string; session_id: string; status?: string }>(`/sessions/${encodeURIComponent(id)}/regenerate`, 'POST',
      { request_id, expected_reply_id, expected_conversation_revision });
  }
  compact(id: string, request_id = crypto.randomUUID()) {
    return this.json<{ run_id: string; session_id: string }>(`/sessions/${encodeURIComponent(id)}/compact`, 'POST', { request_id });
  }
  sessionControlOptions() { return this.json<SessionControlOptions>('/session-controls'); }
  refreshMcp() { return this.json<McpRefreshResult>('/mcp/refresh', 'POST', {}); }
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
  private sessionView(value: SessionView) {
    if (value.live?.messages_location === 'session') value.live = { ...value.live, messages: value.messages };
    return value;
  }
  async session(id: string, signal?: AbortSignal) { return this.sessionView(await this.json<SessionView>(`/sessions/${encodeURIComponent(id)}`, 'GET', undefined, signal)); }
  async prepare(id: string, signal?: AbortSignal) {
    return this.sessionView(await this.json<SessionView>(`/sessions/${encodeURIComponent(id)}/prepare`, 'POST', undefined, signal));
  }
  messages(id: string, before?: string, agentId?: string, signal?: AbortSignal) {
    const query = new URLSearchParams({ ...(before ? { before } : {}), ...(agentId ? { agent_id: agentId } : {}) });
    return this.json<HistoryPage>(`/sessions/${encodeURIComponent(id)}/messages?${query}`, 'GET', undefined, signal);
  }
  messageReasoning(id: string, row: { sequence?: number; revision?: number }, offset: number, conversationRevision: number, agentId?: string) {
    const query = new URLSearchParams({ revision: String(row.revision), offset: String(offset), conversation_revision: String(conversationRevision),
      ...(agentId ? { agent_id: agentId } : {}) });
    return this.json<{ text: string; next_offset: number; complete: boolean }>(`/sessions/${encodeURIComponent(id)}/messages/${row.sequence}/reasoning?${query}`);
  }
  async messageContent(id: string, row: MessageRow, conversationRevision: number, agentId?: string, signal?: AbortSignal) {
    const identity = row.detail;
    if (!identity) throw new ApiError('history_message_unavailable');
    const chunks: string[] = [];
    let offset = 0, characters: number | undefined;
    while (true) {
      signal?.throwIfAborted();
      const query = new URLSearchParams({ revision: String(identity.revision), offset: String(offset),
        conversation_revision: String(conversationRevision), ...(agentId ? { agent_id: agentId } : {}) });
      const part = await this.json<{ revision: number; characters: number; body_json_chunk: string; next_offset: number; complete: boolean }>(
        `/sessions/${encodeURIComponent(id)}/messages/${identity.sequence}/content?${query}`, 'GET', undefined, signal);
      signal?.throwIfAborted();
      if (part.revision !== identity.revision || typeof part.body_json_chunk !== 'string' ||
          !Number.isSafeInteger(part.characters) || part.characters < 1 ||
          !Number.isSafeInteger(part.next_offset) || part.next_offset <= offset || part.next_offset > part.characters ||
          part.next_offset !== offset + Array.from(part.body_json_chunk).length ||
          typeof part.complete !== 'boolean' || part.complete !== (part.next_offset === part.characters) ||
          (characters !== undefined && characters !== part.characters)) throw new ApiError('history_content_invalid');
      characters = part.characters;
      chunks.push(part.body_json_chunk);
      offset = part.next_offset;
      if (part.complete) break;
    }
    let saved;
    try { saved = JSON.parse(chunks.join('')); }
    catch { throw new ApiError('history_content_invalid'); }
    if (saved?.type !== row.type || saved?.data?.id !== row.id ||
        (typeof saved.data.content !== 'string' && !Array.isArray(saved.data.content)))
      throw new ApiError('history_content_invalid');
    return textContent(saved.data.content);
  }
  async fileChange(id: string, row: { sequence?: number; revision?: number }, patch = false, agentId?: string, conversationRevision = 0) {
    if (!row.sequence || !row.revision) throw new ApiError('change_unavailable');
    const query = new URLSearchParams({ revision: String(row.revision), conversation_revision: String(conversationRevision), ...(agentId ? { agent_id: agentId } : {}) });
    const path = `/sessions/${encodeURIComponent(id)}/messages/${row.sequence}/${patch ? 'patch' : 'change'}?${query}`;
    return patch ? (await this.response(path)).text() : this.json<FileChangeView>(path);
  }
  context(id: string) { return this.json<ContextView>(`/sessions/${encodeURIComponent(id)}/context`); }
  usage(id: string, afterModel?: string, signal?: AbortSignal) {
    const query = afterModel === undefined ? '' : '?' + new URLSearchParams({ after_model: afterModel });
    return this.json<SessionUsageView>(`/sessions/${encodeURIComponent(id)}/usage${query}`, 'GET', undefined, signal);
  }
  subagents(id: string, signal?: AbortSignal) { return this.json<SubagentRecord[]>(`/sessions/${encodeURIComponent(id)}/subagents`, 'GET', undefined, signal); }
  subagent(id: string, agentId: string) {
    return this.json<SubagentDetail>(`/sessions/${encodeURIComponent(id)}/subagents/${encodeURIComponent(agentId)}`);
  }
  async recover(id: string, checkpoint_id: string, action: 'continue' | 'restore_approval') {
    return this.sessionView(await this.json<SessionView>(`/sessions/${encodeURIComponent(id)}/recover`, 'POST', { checkpoint_id, action }));
  }
  select(id: string, model_id: string) {
    return this.json<{ model_id: string }>(`/sessions/${encodeURIComponent(id)}/model`, 'PATCH', { model_id });
  }
  start(id: string, text: string, request_id = crypto.randomUUID(), expected_conversation_revision?: number) {
    return this.json<{ run_id: string; session_id: string }>(`/sessions/${encodeURIComponent(id)}/runs`,
      'POST', { text, request_id, expected_conversation_revision });
  }
  control(id: string, kind: 'steering' | 'follow_up' | 'stop', text?: string, agent_path = 'root') {
    return this.json<{ accepted: boolean; retained: string[] }>(`/runs/${encodeURIComponent(id)}/controls`,
      'POST', { kind, request_id: crypto.randomUUID(), ...(text === undefined ? {} : { text }),
        ...(agent_path === 'root' ? {} : { agent_path }) });
  }
  run(id: string, signal?: AbortSignal) { return this.json<RunView>(`/runs/${encodeURIComponent(id)}`, 'GET', undefined, signal); }

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
    session_busy: '当前会话仍在运行或收尾，输入内容已保留。',
    run_capacity_exceeded: '应用运行名额已满，请等待其他任务结束后重试；输入和审批选择已保留。',
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
    history_cursor_stale: '历史消息已修订，请重新准备并同步显示。',
    invalid_history_cursor: '历史分页位置无效，请重新打开会话。',
    history_message_unavailable: '该消息暂时无法显示，可以重试；原运行不会重跑。',
  };
  return messages[code] ?? '操作未确认，内容已保留。请检查连接或重新同步。';
}
