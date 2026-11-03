export type DeliveryAction = 'export' | 'branch' | 'apply';
export interface DeliveryRequest {
  command_id: string; local_session_id: string; receive_id: string;
  action: DeliveryAction; destination_handle: string; name: string;
}
export interface DeliveryRecord {
  command_id: string; local_project_id: string; local_session_id: string; receive_id: string;
  action: DeliveryAction; name: string; phase: 'preparing' | 'prepared' | 'executing' | 'completed' | 'partial' | 'failed' | 'unknown';
  changes: { path: string; operation: 'add' | 'modify' | 'delete' }[]; conflicts: string[];
  error_code: string | null; branch_head: string | null;
}
