import { decodeSSE, responseBytes } from './sse';
import type { SseFrame } from './sse';
import type { RunView, SessionRow, SessionView, ToolDecision } from './types';

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

  models() { return this.json<{ default_model_id: string; model_ids: string[] }>('/models'); }
  sessions() { return this.json<SessionRow[]>('/sessions'); }
  create(model_id: string) { return this.json<{ session_id: string }>('/sessions', 'POST', { model_id }); }
  session(id: string) { return this.json<SessionView>(`/sessions/${encodeURIComponent(id)}`); }
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
  control(id: string, kind: 'steering' | 'follow_up' | 'stop', text?: string) {
    return this.json<{ accepted: boolean; retained: string[] }>(`/runs/${encodeURIComponent(id)}/controls`,
      'POST', { kind, request_id: crypto.randomUUID(), ...(text === undefined ? {} : { text }) });
  }
  run(id: string) { return this.json<RunView>(`/runs/${encodeURIComponent(id)}`); }

  approve(id: string, origin_run_id: string, decisions: ToolDecision[], request_id = crypto.randomUUID()) {
    return this.json<{ run_id: string; session_id: string }>(`/sessions/${encodeURIComponent(id)}/approvals`,
      'POST', { request_id, origin_run_id, decisions });
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
    approval_restore_unavailable: '原审批的目标或权限条件已变化。可核对后重试，或结束旧批次后重新提出任务。',
    manual_review_required: '执行结果需要人工核对，当前不能继续运行。',
    workspace_mismatch: '该会话属于其他工作区，请用对应工作区启动后恢复。',
    resync_required: '事件连接需要重新同步，已保留当前内容。',
    sequence_gap: '事件交付不完整，请重新同步。',
    request_window_full: '当前服务的请求保留窗口已满，请等待运行结束后重启服务。',
    view_too_large: '当前可显示内容超出交付上限。',
    invalid_request: '输入格式无效或内容过长，请检查后重新提交。',
  };
  return messages[code] ?? '操作未确认，内容已保留。请检查连接或重新同步。';
}
