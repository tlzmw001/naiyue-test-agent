import type { ContextSnapshot } from './contextTypes';
export type RunStatus = 'running' | 'completed' | 'stopped' | 'interrupted' | 'failed' | 'unconfirmed';
export type PermissionMode = 'Default' | 'NoAsk' | 'Auto' | 'BypassApprovals';
export interface ThinkingOptions { default_level: string; supported_levels: string[]; }
export interface McpSource { server_id: string; remote_tool: string; transport?: 'stdio' | 'streamable_http'; }
export interface McpServerStatus {
  server_id: string; status: 'enabled' | 'disabled' | 'error'; enabled: boolean;
  selected_tools: string[]; draining?: boolean; error?: string;
}
export interface McpRefreshResult { servers: McpServerStatus[]; }
export interface McpResultView {
  version: 1; source: McpSource;
  outcome: 'success' | 'tool_error' | 'not_executed' | 'outcome_unknown' | 'unsupported_result';
  truncated?: boolean; result_bytes?: number; archive_complete?: boolean;
  archive_paths?: { raw?: string; text?: string }; archive_reason?: string;
}
export interface SessionControlOptions {
  tools: { name: string; description: string; mcp_source?: McpSource; subagent_roles?: string[];
    available?: boolean; unavailable_reason?: string | null }[];
  mcp_servers?: McpServerStatus[];
  permission_modes: PermissionMode[];
  default_permission_mode: PermissionMode;
  default_active_tools: string[];
}

export interface ToolCallRow { id: string; name: string; args: Record<string, unknown>; }
export interface ToolState {
  tool_call_id: string; tool_name: string | null; run_id: string;
  state: 'pending' | 'running' | 'finished' | 'unconfirmed';
  boundary?: string; outcome?: string; reason?: string | null;
  args?: Record<string, unknown>; result?: MessageRow;
  batch_id?: string | null;
  activity?: 'queued' | 'preparing' | 'running' | 'stopping' | 'settling';
  phase?: string; completed?: number | null; total?: number | null;
  unit?: 'bytes' | 'lines' | 'entries' | 'matches' | null; text?: string;
  started_at?: string; last_activity_at?: string; finished_at?: string;
}
export interface ApprovalRequest { approval_id: string; tool_call_id: string; tool_name: string; target: string; workspace?: string; remember_scope?: 'tool'; mcp_source?: McpSource; }
export interface ToolDecision { approval_id: string; decision: 'approve' | 'reject'; }
export interface ApprovalBatch {
  approval_batch_id: string; kind: 'paused_root' | 'live_child'; agent_path: string;
  agent: string | null; origin_run_id: string; items: ApprovalRequest[];
}
export interface AgentEventSource {
  agent_path: string; agent_id: string; agent_run_id: string; parent_run_id: string; parent_tool_call_id: string;
}
export interface AgentView {
  source: AgentEventSource; status: RunStatus | 'awaiting_approval' | 'stopping'; batch_id?: string;
  tool_states: Record<string, ToolState>;
  messages?: MessageRow[];
  parent_batch_id?: string | null;
  snapshot_sequence?: number;
}

export interface SubagentRecord {
  agent_id: string; agent_path: string; agent: string; task: string; tool_call_id: string;
  status: AgentView['status'];
  parent_batch_id?: string | null;
  resume_available?: boolean;
  resume_reason?: string | null;
}
export interface SubagentDetail extends SubagentRecord {
  available: boolean; has_compaction: boolean; messages: MessageRow[];
  has_more?: boolean; next_cursor?: string | null; coverage?: HistoryPage['coverage'];
  tool_states: Record<string, ToolState>; source?: AgentEventSource;
}

export interface FileChangeView {
  type: string; version: number; operation: string; path: string;
  firstChangedLine?: number | null; diff: string; preview: string;
  diff_truncated?: boolean; preview_truncated?: boolean; patch_omitted?: boolean;
  unavailable_reason?: string | null; detail_available?: boolean;
}
export type ReasoningLoader = (row: MessageRow, offset: number) => Promise<{ text: string; next_offset: number; complete: boolean }>;
export type ContentLoader = (row: MessageRow, signal: AbortSignal) => Promise<string>;
export type ChangeLoader = (row: MessageRow, patch?: boolean) => Promise<FileChangeView | string>;

export interface MessageRow {
  images?: import('./imageTypes').ImageMetadata[];
  mcp_result?: McpResultView;
  file_change?: FileChangeView;
  reply_id?: string | null;
  reasoning?: string;
  reasoning_index?: number;
  reasoning_truncated?: boolean;
  id: string;
  sequence?: number;
  revision?: number;
  parent_message_id?: string | null;
  content_truncated?: boolean;
  arguments_truncated?: boolean;
  tool_calls_truncated?: boolean;
  detail?: { sequence: number; revision: number };
  type: string;
  content: string;
  content_projection?: 'text';
  provisional: boolean;
  state: 'draft' | 'complete' | 'unconfirmed';
  is_context_summary?: boolean;
  delta_index?: number;
  content_unavailable?: boolean;
  run_id?: string | null;
  tool_calls?: ToolCallRow[];
  tool_call_id?: string;
  name?: string | null;
  status?: 'success' | 'error';
}

export interface RunView {
  preparation_phase?: 'resources' | 'model_request' | 'model' | 'tools' | null;
  messages_location?: 'session';
  coverage?: { kind: 'window'; first_message_id: string | null };
  mode?: 'message' | 'approval_resume' | 'compact';
  compaction_result?: { status: string; reason?: string | null } | null;
  session_id: string;
  run_id: string;
  status: RunStatus;
  messages: MessageRow[];
  last_sequence: number;
  incomplete: boolean;
  done: boolean;
  manual_review_required: boolean;
  retained?: string[];
  tool_states?: Record<string, ToolState>;
  agents?: Record<string, AgentView>;
  stop_requested?: boolean;
  context_snapshot?: ContextSnapshot | null;
  failure?: { error_type: string; reason: string } | null;
}

export interface SessionRow {
  execution_binding?: 'local' | 'project_local' | 'sandbox' | null;
  title?: string | null;
  pinned?: boolean;
  session_id: string;
  model_id: string;
  workspace_root: string | null;
  state: string;
  updated_at: string;
  last_run_status: string | null;
}

export interface SessionView extends SessionRow {
  execution?: ExecutionView;
  project_id?: string | null;
  execution_root_error?: { code: 'execution_root_unavailable'; reason: 'missing' | 'not_directory' | 'not_registered' | 'wrong_project' | 'unreadable' } | null;
  conversation_revision?: number;
  last_reply_id?: string | null;
  history_ready?: boolean;
  history?: Omit<HistoryPage, 'messages'>;
  thinking_level?: string;
  checkpoint_id?: string | null;
  active_tools: string[];
  permission_mode: PermissionMode;
  messages: MessageRow[];
  busy: boolean;
  can_run: boolean;
  has_compaction: boolean;
  active_run_id: string | null;
  latest_run_id: string | null;
  manual_review_required: boolean;
  pending_approval: boolean;
  live: RunView | null;
  approval_batches?: ApprovalBatch[];
  recovery?: SessionRecovery;
}

export interface HistoryPage {
  messages: MessageRow[];
  has_more: boolean;
  next_cursor: string | null;
  coverage: { checkpoint_id: string | null; sync_revision: number; history_version: number; upper: number };
  has_compaction: boolean;
}

export interface HistoryControls {
  enabled: boolean; hasMore: boolean; loading: boolean; error: string; disabled?: boolean;
  loadOlder: () => Promise<void>;
}

export interface SessionRecovery {
  kind: 'ready' | 'pending_approval' | 'interrupted_execution' | 'unavailable';
  reason: string;
  actions: string[];
  unconfirmed_tool_call_ids: string[];
}

export interface RuntimeEvent {
  version: 1;
  session_id: string;
  run_id: string;
  sequence: number;
  timestamp?: string;
  event_type: string;
  data: Record<string, unknown> & {
    duration_ms?: number | null;
    http_observation_version?: number | null;
    http_capture_status?: 'complete' | 'incomplete' | 'unavailable' | null;
    attempt_id?: string;
    response_headers_ms?: number | null;
    http_status?: number | null;
  };
  source?: AgentEventSource | null;
}
export interface SkillEntry {
  source_id: 'agents' | 'claude'; name: string; description: string; command: string;
}
export interface SkillCatalogView {
  entries: SkillEntry[]; default_source: 'agents' | 'claude' | null;
  diagnostics: { source_id: string; reason: string }[]; read_registered: boolean;
}

export interface PreparationView {
  state: 'preparing' | 'ready' | 'failed' | 'cancelled'; phase: string;
  completed: number | null; total: number | null; error_code: string | null;
}
export interface ExecutionView {
  kind: 'host' | 'sandbox'; state: 'ready' | 'preparing' | 'unavailable' | 'failed';
  binding_id?: string; input_id?: string; root?: string | null;
  error?: { code: string; paths?: string[] } | null;
  profile_id?: string; profile_name?: string; profile_revision?: string;
  preparation?: PreparationView;
}
export interface ExecutionOptions {
  host_available: boolean; sandbox_available: boolean; error_code: string | null;
  requires_trust: boolean; project_revision: number; network: string;
  untracked: string[]; blocked: string[];
  default_profile?: string | null;
  profiles?: { profile_id: string; name: string; revision: string }[];
}
export interface SessionCreation {
  execution: 'host' | 'sandbox'; model_id: string; include_untracked: string[]; requestId: string;
  profile_id?: string; expected_profile_revision?: string;
}
export type ExecutionDraft = Pick<SessionCreation, 'execution' | 'include_untracked' | 'profile_id' | 'expected_profile_revision'> & {
  profile_name?: string; trust_revision?: number;
};
export type DraftControls = Partial<Pick<SessionView, 'active_tools' | 'permission_mode' | 'thinking_level'>>;
