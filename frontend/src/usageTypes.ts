export interface UsageMetric {
  known_sum: string | null;
  reported_calls: number;
  unknown_calls: number;
}

export interface UsageBucket {
  recorded_calls: number;
  status_counts: Record<'started' | 'returned' | 'failed' | 'cancelled' | 'unconfirmed', number>;
  usage_status_counts: Record<'final' | 'partial' | 'unavailable', number>;
  metrics: Record<'input_tokens' | 'output_tokens' | 'cache_read_tokens' | 'cache_write_tokens', UsageMetric>;
}

export interface SessionUsageView extends UsageBucket {
  session_id: string;
  as_of: string;
  activities: (UsageBucket & { id: string })[];
  scopes: (UsageBucket & { id: string })[];
  models: {
    items: (UsageBucket & { model_id: string })[];
    next_after: string | null;
    unattributed: UsageBucket;
  };
  capture: { recorded_runs: number; incomplete_runs: number; dropped_events: string };
}
