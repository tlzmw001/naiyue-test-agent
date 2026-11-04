import type { ImageUpload, ImageConfiguration, ImageMetadata, PendingImageInput } from './imageTypes';
import { CloudApi } from './cloudApi';
import type { ContextView } from './contextTypes';
import type { BackgroundTask, BackgroundTaskOutput, BackgroundTaskPage } from './backgroundTaskTypes';
import type { McpRefreshResult } from './types';
import type { SessionUsageView } from './usageTypes';
import type { DiagnosticEvents, DiagnosticRuns } from './diagnosticTypes';
import { decodeSSE, responseBytes } from './sse';
import { textContent } from './agentEvents';
import type { SseFrame } from './sse';
import type { DirectoryPage, ProjectCatalog, ProjectRow, RegistrationOperation } from './projectTypes';
import type { ApprovalBatch, FileChangeView, HistoryPage, MessageRow, PermissionMode, RunView, SessionControlOptions, SessionRow, SessionView, SkillCatalogView, SubagentDetail, SubagentRecord, ThinkingOptions, ToolDecision } from './types';

export interface ServiceInfo {
  workspace_root: string | null;
  workspace_label: string | null;
}

export class ApiError extends Error {
  constructor(public code: string) { super(code); }
}

export interface BrowserConnection { token: string; scopeId: string; instanceId: string; legacyDatabase?: string }
export function browserConnection(): BrowserConnection {
  const empty = { token: '', scopeId: '', instanceId: '' };
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  let value: BrowserConnection;
  if (window.location.hash) {
    value = { token: fragment.get('access_token') ?? '', scopeId: fragment.get('scope_id') ?? '',
      instanceId: fragment.get('instance_id') ?? '' };
    if (value.scopeId === 'legacy') value.legacyDatabase = fragment.get('legacy_database') ?? '';
    history.replaceState(null, '', window.location.pathname + window.location.search);
  } else {
    try { value = JSON.parse(sessionStorage.getItem('naiyue.service-connection') ?? 'null') ?? empty; }
    catch { value = empty; }
  }
  if (typeof value.token !== 'string' || !/^[a-zA-Z0-9_-]{32,256}$/.test(value.token) ||
      typeof value.scopeId !== 'string' || !/^(chats|legacy|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/.test(value.scopeId) ||
      (value.scopeId === 'legacy' && (typeof value.legacyDatabase !== 'string' || !value.legacyDatabase || value.legacyDatabase.length > 32768)) ||
      typeof value.instanceId !== 'string' || !/^[0-9a-f]{32}$/.test(value.instanceId)) {
    sessionStorage.removeItem('naiyue.service-connection');
    return empty;
  }
  sessionStorage.setItem('naiyue.service-connection', JSON.stringify(value));
  return value;
}

export class WebApi {
  constructor(private token: string, readonly scopeId = 'chats', private instanceId?: string) {}

  forScope(scopeId: string) { return new WebApi(this.token, scopeId, this.instanceId); }
  async projects(signal?: AbortSignal): Promise<ProjectCatalog> {
    return (await this.response('/projects', { signal }, true)).json();
  }
  async directory(scopeId: string, signal?: AbortSignal): Promise<SessionRow[]> {
    return (await this.response(`/navigation/${encodeURIComponent(scopeId)}/sessions`, { signal }, true)).json();
  }
  async browseDirectories(options: { path?: string; show_hidden?: boolean; offset?: number } = {}, signal?: AbortSignal): Promise<DirectoryPage> {
    const query = new URLSearchParams();
    if (options.path !== undefined) query.set('path', options.path);
    if (options.show_hidden !== undefined) query.set('show_hidden', String(options.show_hidden));
    if (options.offset !== undefined) query.set('offset', String(options.offset));
    return (await this.response('/directories?' + query, { signal }, true)).json();
  }
  async getProject(id: string): Promise<ProjectRow> { return (await this.response('/projects/' + encodeURIComponent(id), {}, true)).json(); }
  async projectOperations(): Promise<{ items: RegistrationOperation[] }> { return (await this.response('/projects/operations', {}, true)).json(); }
  private async projectCommand<T>(path: string, body: object, requestId: string = crypto.randomUUID()): Promise<T> {
    return (await this.response('/projects/' + path, { method: 'POST', body: JSON.stringify(body), headers: { 'X-Request-ID': requestId } }, true)).json();
  }
  addLocalProject(root: string, expected_directory: string, requestId: string) {
    return this.projectCommand<ProjectRow>('local', { root, trusted: true, expected_directory }, requestId);
  }
  createLocalProject(parent: string, folder_name: string, expected_directory: string, requestId: string) {
    return this.projectCommand<ProjectRow>('create', { parent, folder_name, trusted: true, expected_directory }, requestId);
  }
  reconcileProject(operation_id: string) { return this.projectCommand<{ items: { operation_id: string; error: string | null }[] }>('reconcile', { operation_id }); }

  private async response(path: string, init: RequestInit = {}, service: boolean | 'client' = false) {
    if (!this.token || !this.scopeId) throw new ApiError('unauthorized');
    const method = (init.method ?? 'GET').toUpperCase();
    const command = !['GET', 'HEAD', 'OPTIONS'].includes(method);
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    const requestId = command ? body?.request_id ?? crypto.randomUUID() : undefined;
    const response = await fetch((service === 'client' ? '/api/client/v1' : service ? '/api/v1' : `/api/v1/scopes/${encodeURIComponent(this.scopeId)}`) + path, { ...init, redirect: 'error', headers: {
      Authorization: 'Bearer ' + this.token, ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...(requestId ? { 'X-Request-ID': requestId } : {}),
      ...(Array.isArray(body?.images) && body.images.length ? { 'X-Image-Input': '1' } : {}),
      ...(this.instanceId ? { 'X-Service-Instance': this.instanceId } : {}),
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

  models() { return this.json<{ default_model_id: string; model_ids: string[]; thinking_options?: Record<string, ThinkingOptions> } & ImageConfiguration>('/models'); }
  cloudTasks() { return new CloudApi(this.scopeId, (path, init) => this.response(path, init, 'client'), (options, signal) => this.browseDirectories(options, signal)); }
  serviceInfo(signal?: AbortSignal) { return this.json<ServiceInfo>('/service', 'GET', undefined, signal); }
  backgroundTasks(before?: string, signal?: AbortSignal) {
    const query = before ? '?' + new URLSearchParams({ before }) : '';
    return this.json<BackgroundTaskPage>('/background-tasks' + query, 'GET', undefined, signal);
  }
  backgroundTaskOutput(id: string, signal?: AbortSignal) {
    return this.json<BackgroundTaskOutput>(`/background-tasks/${encodeURIComponent(id)}/output`, 'GET', undefined, signal);
  }
  stopBackgroundTask(id: string, signal?: AbortSignal) {
    return this.json<BackgroundTask>(`/background-tasks/${encodeURIComponent(id)}/stop`, 'POST', undefined, signal);
  }
  selectThinking(id: string, model_id: string, thinking_level: string) {
    return this.json<{ model_id: string; thinking_level: string }>(`/sessions/${encodeURIComponent(id)}/thinking`, 'PATCH', { model_id, thinking_level });
  }
  regenerate(id: string, expected_reply_id: string, expected_conversation_revision: number, request_id: string = crypto.randomUUID()) {
    return this.json<{ run_id?: string; session_id: string; status?: string }>(`/sessions/${encodeURIComponent(id)}/regenerate`, 'POST',
      { request_id, expected_reply_id, expected_conversation_revision });
  }
  compact(id: string, request_id: string = crypto.randomUUID()) {
    return this.json<{ run_id: string; session_id: string }>(`/sessions/${encodeURIComponent(id)}/compact`, 'POST', { request_id });
  }
  sessionControlOptions() { return this.json<SessionControlOptions>('/session-controls'); }
  refreshMcp() { return this.json<McpRefreshResult>('/mcp/refresh', 'POST', {}); }
  skills(sessionId?: string | null) { return this.json<SkillCatalogView>('/skills'+(sessionId ? '?'+new URLSearchParams({session_id:sessionId}) : '')); }
  selectTools(id: string, active_tools: string[]) {
    return this.json<{ active_tools: string[] }>(`/sessions/${encodeURIComponent(id)}/tools`, 'PATCH', { active_tools });
  }
  selectPermissionMode(id: string, permission_mode: PermissionMode) {
    return this.json<{ permission_mode: PermissionMode }>(`/sessions/${encodeURIComponent(id)}/permission-mode`, 'PATCH', { permission_mode });
  }
  sessions(signal?: AbortSignal) { return this.json<SessionRow[]>('/sessions', 'GET', undefined, signal); }
  updateSession(id: string, change: { title?: string; pinned?: boolean }) {
    return this.json<SessionRow>(`/sessions/${encodeURIComponent(id)}/presentation`, 'PATCH', change);
  }
  reorderSessions(session_ids: string[]) { return this.json<SessionRow[]>('/session-order', 'PUT', { session_ids }); }
  deleteSession(id: string) { return this.json<{ session_id: string }>(`/sessions/${encodeURIComponent(id)}`, 'DELETE'); }
  create(model_id: string, requestId?: string) {
    return this.response('/sessions', { method: 'POST', body: JSON.stringify({ model_id }),
      ...(requestId ? { headers: { 'X-Request-ID': requestId } } : {}) }).then(r => r.json() as Promise<{ session_id: string }>);
  }
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
  diagnosticRuns(id: string, before?: number, signal?: AbortSignal) {
    const query = before === undefined ? '' : '?' + new URLSearchParams({ before: String(before) });
    return this.json<DiagnosticRuns>(`/sessions/${encodeURIComponent(id)}/diagnostics${query}`, 'GET', undefined, signal);
  }
  diagnosticEvents(id: string, runId: string, after = 0, signal?: AbortSignal) {
    const query = new URLSearchParams({ after: String(after) });
    return this.json<DiagnosticEvents>(`/sessions/${encodeURIComponent(id)}/diagnostics/${encodeURIComponent(runId)}?${query}`, 'GET', undefined, signal);
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
  start(id: string, text: string, request_id: string = crypto.randomUUID(), expected_conversation_revision?: number, images: ImageUpload[] = [], pending_input_ids: string[] = [], material_refs: string[] = []) {
    return this.json<{ run_id: string; session_id: string }>(`/sessions/${encodeURIComponent(id)}/runs`,
      'POST', { text, request_id, expected_conversation_revision, ...(images.length ? { images, pending_input_ids } : {}), ...(material_refs.length ? { material_refs } : {}) });
  }
  control(id: string, kind: 'steering' | 'follow_up' | 'stop', text?: string, agent_path = 'root', agent_run_id?: string, approval_batch_id?: string, images: ImageUpload[] = [], pending_input_ids: string[] = [], request_id: string = crypto.randomUUID()) {
    return this.json<{ accepted: boolean; retained: string[] }>(`/runs/${encodeURIComponent(id)}/controls`,
      'POST', { kind, request_id, ...(images.length ? { images, pending_input_ids } : {}), ...(text === undefined ? {} : { text }),
        ...(agent_path === 'root' ? {} : { agent_path }), ...(agent_run_id ? { agent_run_id } : {}), ...(approval_batch_id ? { approval_batch_id } : {}) });
  }
  pendingInputs(id: string) { return this.json<{ inputs: PendingImageInput[] }>(`/sessions/${encodeURIComponent(id)}/pending-inputs`); }
  async pendingInputText(id: string, messageId: string) {
    let offset=0, text='';
    while (true) {
      const page=await this.json<{text:string;next_offset:number;complete:boolean}>(`/sessions/${encodeURIComponent(id)}/pending-inputs/${encodeURIComponent(messageId)}/text?offset=${offset}`);
      text+=page.text;
      if (page.complete) return text;
      if (page.next_offset<=offset) throw new ApiError('invalid_event');
      offset=page.next_offset;
    }
  }
  pendingImage(id: string, messageId: string, index: number) { return this.response(`/sessions/${encodeURIComponent(id)}/pending-inputs/${encodeURIComponent(messageId)}/images/${index}`).then(response => response.blob()); }
  removePendingInput(id: string, messageId: string) { return this.json(`/sessions/${encodeURIComponent(id)}/pending-inputs/${encodeURIComponent(messageId)}`, 'DELETE', { request_id: crypto.randomUUID() }); }
  messageImage(id: string, messageId: string, image: ImageMetadata, revision: number, signal?: AbortSignal) {
    const params = new URLSearchParams({ source_revision: image.source_revision, conversation_revision: String(revision) });
    return this.response(`/sessions/${encodeURIComponent(id)}/messages/${encodeURIComponent(messageId)}/images/${image.index}?${params}`, { signal }).then(response => response.blob());
  }
  run(id: string, signal?: AbortSignal) { return this.json<RunView>(`/runs/${encodeURIComponent(id)}`, 'GET', undefined, signal); }

  approve(id: string, batch: ApprovalBatch, decisions: ToolDecision[], remember_approved: string[] = [], request_id: string = crypto.randomUUID()) {
    return this.json<{ run_id: string; session_id: string; kind?: 'live_child'; accepted?: boolean }>(`/sessions/${encodeURIComponent(id)}/approvals`,
      'POST', { request_id, origin_run_id: batch.origin_run_id, approval_batch_id: batch.approval_batch_id, decisions, remember_approved });
  }

  stopApproval(id: string, batch: ApprovalBatch, request_id: string = crypto.randomUUID()) {
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
    cloud_material_not_found: '找不到已接收材料，请核对本机接收记录后重新发送。',
    cloud_material_unavailable: '本机材料缺失、损坏或尚未完成校验，输入已保留，请先完成接收。',
    cloud_material_identity_mismatch: '材料不属于当前项目或会话，输入已保留。',
    cloud_material_budget: '材料引用数量或大小超过配置上限，请减少材料后重试。',
    cloud_material_binary: '此材料为二进制文件，只能查看元信息或下载。',
    cloud_materials_unavailable: '当前会话的材料读取能力尚未就绪，输入已保留。',
    unsupported_execution_binding: '旧版沙箱会话不再支持，请新建云端任务。',
    session_creation_unconfirmed: '会话创建结果尚未确认，请重试原请求。',
    service_instance_mismatch: '服务已重启，请从启动终端的新链接重新打开；当前输入已保留。',
    service_closing: '服务正在关闭，请稍后重新启动并打开访问链接。',
    project_trust_required: '项目尚未信任，当前不能执行。',
    project_execution_unavailable: '项目执行目录不可用，历史仍可查看。',
    project_history_unavailable: '会话目录读取失败，请核对项目存储后重试。',
    project_not_found: '项目已移除或不在当前服务中，请刷新项目目录。',
    project_operation_pending: '项目正在处理其他操作，请稍后刷新。',
    project_invalid_path: '请输入有效的本机绝对路径，也可以使用 ~ 表示主文件夹。',
    project_directory_missing: '文件夹不存在，请选择其他位置或刷新后重试。',
    project_directory_not_directory: '这个路径不是文件夹，请重新选择。',
    project_directory_unreadable: '无法读取这个文件夹，请检查访问权限或选择其他位置。',
    project_location_changed: '文件夹在选择后发生了变化，请返回目录重新读取并确认。',
    project_directory_exists: '同名文件夹已经存在，请更换名称，或通过“选择已有文件夹”添加。',
    project_invalid_name: '文件夹名称无效，请使用单个名称，避免路径分隔符、结尾的空格或点。',
    project_legacy_import_required: '这个目录有旧版项目身份，请先通过终端的正式导入流程打开，目录选择器不会覆盖旧身份。',
    project_identity_conflict: '项目身份不一致，请核对目录与已有登记。',
    project_operation_not_found: '尚未找到这次登记记录，请核对后再用同一请求重试。',
    project_operation_failed: '项目登记未完成，请核对操作结果；已经创建的文件夹会保留。',
    image_input_format_unsupported: '图片格式不受支持，请使用 PNG、JPEG、WebP、GIF 或 BMP。',
    image_input_invalid: '图片无法解码或格式与声明不一致，请检查后重试。',
    image_input_too_large: '图片大小超过配置上限，请缩小图片或减少附件。',
    image_input_pixel_limit: '图片像素超过配置上限，请缩小后重试。',
    image_input_busy: '正在处理其他图片，请稍后重试；输入已保留。',
    pending_image_invalid: '保留输入的状态已变化，请刷新保留列表后重试。',
    pending_image_limit: '这个会话保留的图片输入已达到额度，请先处理或移除保留项。',
    model_image_input_unsupported: '当前模型不支持或尚未确认支持图片，请切换模型或移除图片。',
    image_revision_changed: '图片所属消息版本已变化，请刷新会话后重试。',
    image_not_ready: '图片尚未保存完成，请稍后点击重试。',
    image_not_found: '图片不存在或不属于当前会话。',
    image_request_header_required: '图片发送协议未匹配，请刷新页面后重试，输入已保留。',
    unauthorized: '访问链接已失效，请从启动终端重新打开。',
    invalid_host: '请使用启动时提供的本地地址。',
    invalid_origin: '请求来源不匹配，请从本地页面操作。',
    session_busy: '当前会话仍在运行或收尾，输入内容已保留。',
    run_capacity_exceeded: '应用运行名额已满，请等待其他任务结束后重试；输入和审批选择已保留。',
    stale_run: '这次运行已结束，请刷新状态后操作。',
    stale_approval: '审批批次已变化，请重新同步后核对当前调用。',
    approval_expired: '项目位置或登记信息已变化，原审批已过期。请结束旧批次，再重新提出任务进行审查。',
    session_recovery_blocked: '该会话尚不能安全恢复，请先核对之前的执行状态。',
    checkpoint_mismatch: '已保存状态发生变化，请核对刷新后的历史再操作。',
    session_save_unconfirmed: '恢复保存未确认，请核对当前历史后再操作。',
    session_delete_unconfirmed: '删除未确认，会话内容已保留，请刷新核对后重试。',
    session_background_tasks_active: '该会话仍有后台命令或未确认的清理，请先停止后台任务后再删除。',
    session_cloud_delivery_active: '该会话仍在准备或执行代码交付，请等待结束后再删除。',
    task_not_found: '后台任务不存在或不在当前工作区，请刷新列表。',
    task_cursor_invalid: '后台任务分页位置已失效，请刷新列表。',
    task_cleanup_unconfirmed: '进程清理尚未确认，请核对任务状态；任务记录仍保留。',
    background_tasks_unavailable: '后台任务状态读取或操作未确认，请刷新后核对。',
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
