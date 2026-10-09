export type RunStatus = 'running' | 'completed' | 'stopped' | 'interrupted' | 'failed' | 'unconfirmed';

export interface ToolCallRow { id: string; name: string; args: Record<string, unknown>; }
export interface ToolState {
  tool_call_id: string; tool_name: string | null; run_id: string;
  state: 'running' | 'finished' | 'unconfirmed';
  boundary: string; outcome?: string; reason?: string | null;
}
export interface ApprovalRequest { tool_call_id: string; tool_name: string; target: string; workspace?: string; }
export interface ToolDecision { tool_call_id: string; decision: 'approve' | 'reject'; }

export interface MessageRow {
  id: string;
  type: string;
  content: string;
  provisional: boolean;
  state: 'draft' | 'complete' | 'unconfirmed';
  is_context_summary?: boolean;
  delta_index?: number;
  run_id?: string | null;
  tool_calls?: ToolCallRow[];
  tool_call_id?: string;
  name?: string | null;
  status?: 'success' | 'error';
}

export interface RunView {
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
  failure?: { error_type: string; reason: string } | null;
}

export interface SessionRow {
  session_id: string;
  model_id: string;
  workspace_root: string | null;
  state: string;
  updated_at: string;
  last_run_status: string | null;
}

export interface SessionView extends SessionRow {
  messages: MessageRow[];
  busy: boolean;
  can_run: boolean;
  has_compaction: boolean;
  active_run_id: string | null;
  latest_run_id: string | null;
  manual_review_required: boolean;
  pending_approval: boolean;
  live: RunView | null;
  approval_run_id?: string | null;
  approval_requests?: ApprovalRequest[];
}

export interface RuntimeEvent {
  version: 1;
  session_id: string;
  run_id: string;
  sequence: number;
  event_type: string;
  data: Record<string, unknown>;
}
