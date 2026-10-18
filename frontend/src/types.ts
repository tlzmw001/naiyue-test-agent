import type { ContextSnapshot } from './contextTypes';
export type RunStatus = 'running' | 'completed' | 'stopped' | 'interrupted' | 'failed' | 'unconfirmed';
export type PermissionMode = 'Default' | 'NoAsk' | 'Auto' | 'BypassApprovals';
export interface ThinkingOptions { default_level: string; supported_levels: string[]; }
export interface SessionControlOptions {
  tools: { name: string; description: string }[];
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
export interface ApprovalRequest { approval_id: string; tool_call_id: string; tool_name: string; target: string; workspace?: string; }
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
}
export interface SubagentDetail extends SubagentRecord {
  available: boolean; has_compaction: boolean; messages: MessageRow[];
  has_more?: boolean; next_cursor?: string | null; coverage?: HistoryPage['coverage'];
  tool_states: Record<string, ToolState>; source?: AgentEventSource;
}

export interface MessageRow {
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
  data: Record<string, unknown>;
  source?: AgentEventSource | null;
}
export interface SkillEntry {
  source_id: 'agents' | 'claude'; name: string; description: string; command: string;
}
export interface SkillCatalogView {
  entries: SkillEntry[]; default_source: 'agents' | 'claude' | null;
  diagnostics: { source_id: string; reason: string }[]; read_registered: boolean;
}
