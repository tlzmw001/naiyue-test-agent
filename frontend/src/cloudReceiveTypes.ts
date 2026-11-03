import type { CloudResult } from './cloudResultTypes';

export interface ReceivedArtifact {
  command_id: string; artifact_id: string; relative_name: string; task_id: string; project_id: string;
  input_id: string; generation: number; size: number; sha256: string; captured_at: number;
}
export interface ReceiveFile {
  material_id: string; role: 'manifest' | 'patch' | 'artifact'; artifact: ReceivedArtifact;
  offset: number; verified: boolean;
}
export interface ReceiveRecord {
  receive_id: string; command_id: string; local_project_id: string; local_session_id: string;
  binding_id: string; instance_id: string; task_id: string; result_id: string; input_id: string;
  phase: 'reserved' | 'downloading' | 'verifying' | 'available' | 'failed' | 'unknown';
  files: ReceiveFile[]; result: CloudResult | null; input_package: unknown | null;
  error_code: string | null; active: boolean; created_at: number; updated_at: number;
}
export interface MaterialRef {
  material_id: string; receive_id: string; task_id: string; result_id: string; input_id: string;
  title: string; role: 'manifest' | 'patch' | 'artifact'; size: number; sha256: string; source_run_id: string;
  outcome: 'completed' | 'stopped' | 'failed' | 'interrupted' | 'unconfirmed'; captured_at: number; reported: true;
}
export interface AnalysisDraft {
  local_project_id: string; local_session_id: string; receive_id: string; text: string; material_refs: MaterialRef[];
}
