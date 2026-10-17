export interface ContextGroup {
  id: string; tokens: number; count?: number; source?: 'AGENTS.md' | 'CLAUDE.md';
  children?: readonly ContextGroup[];
}
export interface ContextEstimate {
  status: 'available' | 'unavailable'; estimated_input_tokens: number | null;
  context_window_tokens: number | null; max_output_tokens: number | null;
  compaction_reserve_tokens: number | null; trigger_threshold_tokens: number | null;
  context_window_source: string; max_output_source: string; compaction_reserve_source: string;
  estimator: string; image_count: number; groups: readonly ContextGroup[];
}
export interface ContextSnapshot {
  version: 1; model_call_id: string; model_id: string | null; run_id: string | null;
  recorded_at: string; status: 'running' | 'returned' | 'failed'; estimate: ContextEstimate;
  usage: { input_tokens: number | null; output_tokens: number | null; cache_read_tokens: number | null } | null;
}
export interface ContextView {
  session_id: string;
  current: { basis: 'next_request' | 'last_sent' | 'unavailable'; model_id?: string | null; estimate: ContextEstimate | null };
  latest_request: ContextSnapshot | null;
}
