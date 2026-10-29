import type { PreparationView } from './types';
export type SandboxAction = 'start' | 'stop' | 'recover' | 'destroy';
export interface ManagedSandbox {
  binding_id: string; project_id: string; session_id: string;
  project_name: string | null; profile_name: string | null; lifecycle: string;
  preparation?: PreparationView | null;
  last_used_at: number | null; error_code: string | null; busy: boolean; unsettled: boolean;
}
export interface SandboxManagement {
  policy: { max_running: number; max_retained: number; idle_stop_seconds: number; sweep_seconds: number };
  usage: { running: number; retained: number };
  items: ManagedSandbox[];
}
