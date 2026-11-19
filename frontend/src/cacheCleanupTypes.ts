export const cleanupCategories = ['tool_outputs', 'cloud_inputs'] as const;
export type CleanupCategory = typeof cleanupCategories[number];
export type CleanupPolicy = { id: CleanupCategory; retention_days: number; available: boolean };
export type CleanupCounts = {
  scopes_visited: number; units_examined: number; units_deleted: number; files_deleted: number;
  bytes_deleted: string; units_retained: number; units_skipped: number; units_failed: number;
};
export type CleanupDetail = {
  category: CleanupCategory; scope_kind: 'project' | 'chat' | 'service';
  project_id?: string; session_id?: string; unit_id?: string; path: string; step: string;
  outcome: 'skipped' | 'failed' | 'unconfirmed'; code: string; reason: string; hint: string;
  errno?: number; winerror?: number;
};
export type CleanupResult = {
  request_id: string; scope: string; started_at_ns: string; finished_at_ns: string;
  policies: Array<{ id: CleanupCategory; retention_days: number; cutoff_ns: string }>;
  status: 'completed' | 'partial' | 'failed'; enumeration_complete: boolean; stop_reason: string | null;
  totals: CleanupCounts; categories: Partial<Record<CleanupCategory, CleanupCounts>>; details: CleanupDetail[];
};
export type CleanupReceipt = { state: 'running'; request_id: string } |
  { state: 'finished'; request_id: string; http_status: number; result?: CleanupResult; error?: { code: string } };
