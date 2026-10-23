export interface BackgroundTask {
  kind?: 'shell' | 'memory'; result_status?: 'completed' | 'partial' | 'failed' | 'stopped';
  task_id: string; session_id: string; workspace_root: string; origin_run_id: string;
  tool_call_id: string; shell: string; command_preview: string;
  status: 'starting' | 'running' | 'stopping' | 'finished' | 'lost';
  created_at: string; started_at: string | null; finished_at: string | null;
  max_execution_seconds: number | null; exit_code: number | null; termination: string | null;
  output_complete: boolean; cleanup_confirmed: boolean; error_type: string | null;
}
export interface BackgroundTaskPage {
  items: BackgroundTask[]; next_before: string | null; active_count: number;
  revision: string; terminal_revision: number;
}
export interface BackgroundTaskOutput extends BackgroundTask {
  output: string; truncated: boolean; observed_raw_bytes: number; observed_text_bytes: number;
  lossy: boolean; archive_paths: Record<string, string>; archive_saved_bytes: number;
  archive_complete: boolean; archive_reason: string | null; output_available: boolean;
}
