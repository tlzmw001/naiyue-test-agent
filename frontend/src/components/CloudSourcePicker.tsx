import { useEffect, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import { cloudError } from '../cloudPresentation';
import styles from './CloudTasks.module.css';
export interface SourceScope { mode: 'git' | 'selection'; paths: string[]; exclude?: string[] }
export interface SourceTree { git: boolean; git_error: string | null; path: string; entries: { path: string; name: string; kind: string; size: number }[]; next_offset: number | null; excluded_count: number }
export interface SourcePreview { file_count: number; total_bytes: number; issues: { code: string; path?: string; actual: number; limit: number }[]; limits: { max_input_bytes: number } }
const size = (n: number) => `${(n / 1024 / 1024).toFixed(2)} MiB`;
export default function CloudSourcePicker({ api, sid, disabled, onChange }: { api: CloudApi; sid: string; disabled: boolean; onChange: (value: SourceScope | null) => void }) {
  const [listing, setListing] = useState<SourceTree>(); const [mode, setMode] = useState<'git' | 'selection'>('selection');
  const [all, setAll] = useState(false); const [paths, setPaths] = useState(''); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(''); const [preview, setPreview] = useState<SourcePreview>();
  const [excluded, setExcluded] = useState<string[]>([]);
  const selected = paths.split('\n').map(p => p.trim()).filter(Boolean);
  const covers = (parent: string, child: string) => child === parent || child.startsWith(parent + '/');
  const checked = (path: string) => selected.some(p => covers(p, path)) && !excluded.some(p => covers(p, path));
  function toggle(path: string, value: boolean) {
    invalidate();
    if (value) { setExcluded(old => old.filter(p => !covers(p, path) && !covers(path, p))); if (!selected.some(p => covers(p, path))) setPaths([...selected, path].join('\n')); }
    else { setPaths(selected.filter(p => !covers(path, p)).join('\n')); if (selected.some(p => p !== path && covers(p, path))) setExcluded(old => [...old.filter(p => !covers(path, p)), path]); }
  }
  useEffect(() => {
    const abort = new AbortController(); setBusy(true);
    api.sourceTree(sid, '', 0, abort.signal).then(value => { if (!abort.signal.aborted) { setListing(value); setMode(value.git ? 'git' : 'selection'); setAll(value.git); } })
      .catch(e => { if (!abort.signal.aborted) { console.warn('cloud_source_tree_failed'); setError(cloudError(e)); } })
      .finally(() => { if (!abort.signal.aborted) setBusy(false); });
    return () => abort.abort();
  }, [api, sid]);
  function invalidate() { setPreview(undefined); onChange(null); setError(''); }
  async function browse(path: string, offset = 0) {
    setBusy(true); setError('');
    try { const value = await api.sourceTree(sid, path, offset); setListing(old => offset && old ? { ...value, entries: [...old.entries, ...value.entries] } : value); }
    catch (e) { console.warn('cloud_source_tree_failed'); setError(cloudError(e)); }
    finally { setBusy(false); }
  }
  async function check() {
    setBusy(true); invalidate(); const value: SourceScope = { mode, paths: mode === 'git' && all ? [] : selected, ...(excluded.length ? { exclude: excluded } : {}) };
    try { const result = await api.sourcePreview(sid, value); setPreview(result); if (!result.issues.length) onChange(value); }
    catch (e) { console.warn('cloud_source_preview_failed'); setError(cloudError(e)); }
    finally { setBusy(false); }
  }
  return <fieldset disabled={disabled || busy} className={styles.sourcePicker}>
    <legend>导入项目文件</legend>
    <label>代码来源<select value={mode} onChange={e => { invalidate(); setMode(e.target.value as 'git' | 'selection'); setAll(e.target.value === 'git'); setExcluded([]); }}>
      <option value="git" disabled={!listing?.git}>项目当前 Git 状态（含未提交改动）</option><option value="selection">选择项目文件和目录</option>
    </select></label>
    {listing?.git_error && <p className={styles.note}>{cloudError({ code: listing.git_error })}</p>}
    {mode === 'git' && <label><input type="checkbox" checked={all} onChange={e => { invalidate(); setAll(e.target.checked); if (e.target.checked) setExcluded([]); }} />导入全部 Git 项目文件</label>}
    {!(mode === 'git' && all) && <>
      <div className={styles.scopeNavigation}><span>当前位置：{listing?.path || '项目根目录'}</span>{listing?.path && <button type="button" onClick={() => void browse(listing.path.split('/').slice(0, -1).join('/'))}>上一级</button>}</div>
      {listing?.path && excluded.some(p => covers(p, listing.path)) && <p className={styles.note}>此目录已排除，请先返回上一级重新选择该目录，再调整其中的文件。</p>}
      <div className={styles.scopeFiles}>{listing?.entries.map(row => <div key={row.path} className={styles.scopeFile}>
        <label><input type="checkbox" aria-label={`选择 ${row.path}`} checked={checked(row.path)} disabled={excluded.some(p => p !== row.path && covers(p, row.path))} onChange={e => toggle(row.path, e.target.checked)} />{row.name}{row.kind === 'directory' ? '/' : ''}</label>
        {row.kind === 'directory' && <button type="button" onClick={() => void browse(row.path)}>打开 {row.name}</button>}
      </div>)}</div>
      {listing?.next_offset != null && <button type="button" onClick={() => void browse(listing.path, listing.next_offset!)}>加载更多文件</button>}
      {!!listing?.excluded_count && <p className={styles.note}>当前目录有 {listing.excluded_count} 项因路径或安全规则排除。</p>}
      <details><summary>已选路径 / 手动填写（{selected.length} 项）</summary><label>文件或目录，每行一项<textarea value={paths} rows={4} onChange={e => { invalidate(); setPaths(e.target.value); setExcluded([]); }} placeholder={'src\ntests\npyproject.toml'} /></label>
        {!!excluded.length && <p>已排除：{excluded.join('、')} <button type="button" onClick={() => { invalidate(); setExcluded([]); }}>清除排除项</button></p>}
        <p className={styles.note}>相对于当前项目根目录，目录会递归导入。支持 / 和 Windows 分隔符，不支持绝对路径、上级路径或通配符。</p></details>
    </>}
    <button type="button" disabled={!listing || (!(mode === 'git' && all) && !selected.length)} onClick={() => void check()}>预览导入范围</button>
    {busy && <p role="status">正在读取项目范围…</p>}
    {preview && <div role="status"><p>{preview.file_count} 个文件 · {size(preview.total_bytes)} / 上限 {size(preview.limits.max_input_bytes)}</p>
      {preview.issues.map((issue, i) => <p key={i} className={styles.error}>{cloudError({ code: issue.code })}{issue.path && `：${issue.path}`}，{size(issue.actual)} / {size(issue.limit)}</p>)}</div>}
    {error && <p role="alert" className={styles.error}>{error}</p>}
  </fieldset>;
}
