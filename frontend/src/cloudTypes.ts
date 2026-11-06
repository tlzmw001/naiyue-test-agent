import type { MessageRow, ToolState, AgentEventSource, ThinkingOptions } from './types';

export interface CloudProfile { connection_id?: string; network_id?: string | null; profile_id: string; template_ids: string[]; default_template: string; connection: string; tunnel: string }
export interface CloudTask {
  task_id: string; project_id: string; input_id: string | null; generation: number; revision: number; lifecycle: string;
  title?: string; created_at?: number; updated_at?: number;
  runner_phase: string; environment_phase: string; sandbox_id: string | null; active_run_id: string | null;
  last_run_id: string | null; last_error_code: string | null; last_quiescent_at: number | null;
  environment_operation_id: string | null; control_operation_id: string | null; artifact_operation_id: string | null;
  runner_capabilities?: string[]; runner_model_ids: string[]; reclaim_blockers: string[];
  runner_recovery?: { run_id: string; checkpoint_id?: string | null } | null;
}
export interface CloudBinding {
  created_at?: number;
  binding_id: string; local_session_id: string; profile_id: string; instance_id: string;
  task_id: string | null; input_id: string | null; revision: number; preparation: string; retained: boolean;
  create_command_id: string; cloud: CloudTask | null;
}
export interface CloudOperation {
  command_id: string; binding_id: string; kind: string; phase: string; error_code?: string | null; run_id?: string | null;
  steps: Record<string, { receipt?: { status: string; error_code?: string | null } }>;
}
export interface CloudDraft { attachment_ids?: string[]; draft_revision: number; text: string; model_id: string | null; thinking_level: string }
export interface CloudModels { default_model_id: string | null; model_ids: string[]; thinking_options: Record<string, ThinkingOptions> }
export interface CloudDetail { message_id: string; thread_id: string; revision: number }
export type CloudMessage = Omit<MessageRow, 'detail'> & { detail?: CloudDetail; thread_id?: string; run_id?: string; source?: AgentEventSource | null };
export interface CloudArchive {
  epoch: string; coverage_seq: number; messages: CloudMessage[]; next_cursor: string | null;
  thread: { thread_id: string; role?: string } | null;
}
export interface CloudLive {
  epoch: string; seq: number; replace: boolean; available: boolean; capture_incomplete: boolean;
  messages: CloudMessage[]; tools: (Omit<ToolState, 'state'> & { state?: ToolState['state']; event_type?: string; source?: AgentEventSource | null; thread_id: string })[];
  threads: { thread_id: string; source: AgentEventSource | null }[];
}
export interface CloudSnapshot { task: Pick<CloudTask, 'task_id' | 'generation' | 'revision' | 'lifecycle' | 'runner_phase' | 'active_run_id'>; archive: CloudArchive; live: CloudLive; cursor: string }
export interface CloudThread { thread_id: string; role: string; source?: AgentEventSource | null; agent?: string | null }
export interface DeletePreview {
  instance_id: string; task_id: string; revision: number; generation: number; lifecycle: string;
  scope: string[]; requires_stop: boolean; preview_digest: string;
}
export interface CloudDetailPage extends CloudDetail { field: string; text: string; offset: number; next_offset: number | null; body_sha256: string }
export interface CloudUsage { recorded_calls: number; metrics?: Record<string, { known_sum: string | null; reported_calls: number; unknown_calls: number }>; capture: { capture_incomplete: boolean } }

export interface CloudAttachment { uploaded_bytes?: number | null; transfer_bytes?: number | null; attachment_id: string; binding_id: string; display_name: string; input_id: string | null; size: number; phase: string; error_code: string | null; created_at: number }
export interface CloudQueuedMessage { command_id: string; message_command_id: string; phase: string; error_code: string | null; request: { text: string; attachment_ids?: string[] }; dispatch_request?: object }

export interface CloudFileTree { path: string; entries: { path: string; name: string; kind: string; size: number }[]; next_offset: number | null }

export interface CloudMcpStatus {
  status: 'available' | 'refreshing' | 'waiting_run' | 'waiting_runner' | 'error' | 'unconfirmed' | 'unsupported';
  refresh_id: string | null;
  services: {
    server_id: string; route: 'direct' | 'windows_tunnel'; tools: string[];
    status: 'available' | 'disabled' | 'error'; error?: string | null;
  }[];
}
