export interface DiagnosticSummary {
  session_id: string;
  run_id: string;
  started_at: string | null;
  status: string | null;
  duration_ms?: number | null;
  capture_state: 'open' | 'closed' | 'interrupted';
  capture_incomplete: boolean;
  dropped_events: number;
  http_attempts?: {
    status: 'complete' | 'partial' | 'unavailable';
    observed_attempts: number | null;
    response_headers: number | null;
    unconfirmed: number | null;
  };
}

export interface DiagnosticEvent {
  session_id: string;
  run_id: string;
  sequence: number;
  timestamp: string;
  event_type: string;
  source: { agent_path: string; agent_id: string; agent_run_id: string } | null;
  data: Record<string, unknown>;
}

export interface DiagnosticRuns {
  session_id: string;
  runs: DiagnosticSummary[];
  next_before: number | null;
}

export interface DiagnosticEvents {
  session_id: string;
  run_id: string;
  summary: DiagnosticSummary;
  events: DiagnosticEvent[];
  next_after: number | null;
}
