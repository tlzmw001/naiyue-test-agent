export interface ProjectRow {
  project_id: string;
  name: string;
  source: { path: string } | { url: string; ref: string };
  lifecycle: 'pending' | 'active' | 'migrating' | 'deleting';
  trusted: boolean;
  revision: number;
  availability: string | null;
}
export interface ProjectCatalog { items: ProjectRow[]; default_parent_dir: string | null }
export interface ScopeSelection { scopeId: string; sessionId?: string; revision: number }
export interface DirectoryPage {
  path: string; parent: string | null; separator: string; identity: string;
  items: { name: string; path: string; symlink: boolean }[]; next_offset: number | null;
  positions: { name: string; path: string }[]; default_parent_dir: string | null; default_error: string | null;
}
export type ProjectPickerMode = 'projects' | 'select' | 'create';
export interface FolderChoice { mode: 'select' | 'create'; path: string; identity: string; target: string; folderName?: string }
export interface RegistrationOperation { operation_id: string; project_id: string; stage: string; error_code: string | null }
