export interface ResultOptions { untracked: string[]; blocked: string[] }
export interface ResultFile {
  path: string; old_path: string; status: string; old_mode: string; new_mode: string;
  symlink: boolean; attributes: boolean;
}
export interface ResultReceipt {
  input_snapshot: string; original_head: string; output_commit: string;
  result_id: string; input_id: string; binding_id: string; project_id: string; session_id: string;
  bundle_size: number; bundle_sha256: string; excluded: {path: string; reason: string}[];
}
export interface SandboxResult {
  discarded?:boolean;
  result_id: string; state: string; operation_state: string; created_at: string;
  include_untracked: string[]; stale?: boolean; error_code?: string | null;
  receipt: ResultReceipt | null; files: ResultFile[]; warnings: {code: string; paths: string[]}[];
}
export interface ResultDiff extends ResultFile { binary: boolean; patch: string | null }
