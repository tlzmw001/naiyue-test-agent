export interface ResultSummary { result_id: string; source_run_id: string; phase: string; revision: number; outcome: string | null; error_code: string | null; captured_at: number | null; files_count: number | null }
export interface ResultFile { role: 'manifest' | 'patch' | 'artifact'; artifact: { artifact_id: string; relative_name: string; size: number; sha256: string } }
export interface CloudResult { plan: { result_id: string; source_run_id: string; input_id: string }; phase: string; revision: number; error_code: string | null; boundary: { outcome: string } | null; bundle: { captured_at: number; files: ResultFile[] } | null }
export interface ResultChanges { changes: { path: string; kind: string; portable: boolean }[]; total: number; next_offset: number | null }
export interface ResultPreview { text: string; binary: boolean; next_offset: number; complete: boolean }
