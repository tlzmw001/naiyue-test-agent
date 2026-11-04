export type DeliveryAction = 'export' | 'branch' | 'apply' | 'files';
export interface DeliveryRequest {
  command_id: string; local_session_id: string; receive_id: string;
  action: DeliveryAction; destination_handle: string; name: string; destination_path?: string; expected_directory?: string;
}
export interface DeliveryRecord {
  command_id: string; local_project_id: string; local_session_id: string; receive_id: string;
  action: DeliveryAction; name: string; phase: 'preparing' | 'prepared' | 'executing' | 'completed' | 'partial' | 'failed' | 'unknown';
  changes: { path: string; operation: 'add' | 'modify' | 'delete' }[]; conflicts: string[];
  force?: boolean; force_allowed?: boolean; base_result_id?: string | null; destination_path?: string | null; done?: string[];
  error_code: string | null; branch_head: string | null;
}
