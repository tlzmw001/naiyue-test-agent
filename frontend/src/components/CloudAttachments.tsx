import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudAttachment } from '../cloudTypes';
import type { CloudFileTree } from '../cloudTypes';
import { cloudError } from '../cloudPresentation';
import { keepDialogFocus } from '../dialogFocus';
import DirectoryBrowser from './DirectoryBrowser';
import type { FolderChoice } from '../projectTypes';
import { Icon } from './Icons';
import composer from './ComposerControls.module.css';
import styles from './CloudTasks.module.css';

const labels: Record<string, string> = { uploading: '上传中', ready: '待发送', attached: '已提交', failed: '需处理', cancelled: '已取消' };
export default function CloudAttachments({ api, sid, bid, rows, selected, select, changed }: {
  api: CloudApi; sid: string; bid: string; rows: CloudAttachment[]; selected: string[];
  select: (ids: string[]) => void; changed: () => void;
}) {
  const [menu, setMenu] = useState(false); const [open, setOpen] = useState(false);
  const [location, setLocation] = useState<FolderChoice>(); const [locations, showLocations] = useState(false);
  const [tree, setTree] = useState<CloudFileTree>(); const [paths, setPaths] = useState<string[]>([]);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null); const trigger = useRef<HTMLButtonElement>(null);
  const intent = useRef<{ attachment_id: string; display_name: string; paths: string[]; directory?: string; directory_identity?: string } | undefined>(undefined);
  const generation = useRef(0);
  useEffect(() => { if (open) { dialog.current?.showModal(); void browse('', 0, location); } return () => { generation.current++; }; }, [open]);
  function close() { showLocations(false); dialog.current?.close(); setOpen(false); trigger.current?.focus(); }
  async function browse(path: string, offset = 0, source = location) {
    const ticket = ++generation.current; setBusy(true); setError('');
    try { const value = await api.files(sid, path, offset, undefined, source); if (ticket === generation.current) setTree(old => offset && old ? { ...value, entries: [...old.entries, ...value.entries] } : value); }
    catch (e) { if (ticket === generation.current) { console.warn('cloud_files_browse_failed'); setError(cloudError(e)); } }
    finally { if (ticket === generation.current) setBusy(false); }
  }
  const covered = (path: string) => paths.some(parent => path.startsWith(parent + '/'));
  async function upload() {
    if (busy || !paths.length) return; setBusy(true); setError('');
    const fixed = intent.current ?? { attachment_id: crypto.randomUUID(), display_name: paths.length === 1 ? paths[0].split('/').at(-1)! : `${paths.length} 个所选项`, paths, ...(location && { directory: location.path, directory_identity: location.identity }) };
    intent.current = fixed;
    try { const item = await api.uploadFiles(sid, bid, fixed); select([...new Set([...selected, item.attachment_id])]); changed(); intent.current = undefined; setPaths([]); close(); }
    catch (e) { console.warn('cloud_files_upload_failed'); setError(cloudError(e)); }
    finally { setBusy(false); }
  }
  async function action(id: string, value: 'cancel' | 'retry') {
    try { await api.attachmentAction(sid, bid, id, value); if (value === 'cancel') select(selected.filter(x => x !== id)); changed(); setError(''); }
    catch (e) { console.warn('cloud_attachment_action_failed'); setError(cloudError(e)); }
  }
  return <div className={styles.attachments}>
    <div className={composer.controls} onKeyDown={e => { if (e.key === 'Escape' && menu) { setMenu(false); trigger.current?.focus(); } }}>
      <button ref={trigger} className={`${composer.trigger} ${composer.plus}`} aria-label="添加附件" aria-expanded={menu} onClick={() => setMenu(v => !v)}><Icon name="plus" /></button>
      {menu && <div className={composer.popover}>
        <div className={composer.heading}>添加附件</div>
        <button className={composer.command} onClick={() => { setMenu(false); setOpen(true); }}>添加文件</button>
        <button className={composer.command} onClick={() => { setMenu(false); showLocations(true); setOpen(true); }}>浏览此电脑</button>
      </div>}
    </div>
    {rows.filter(row => row.phase !== 'cancelled').map(row => <div key={row.attachment_id} className={styles.attachmentRow}>
      <label><input type="checkbox" checked={selected.includes(row.attachment_id)} onChange={e => select(e.target.checked ? [...selected, row.attachment_id] : selected.filter(x => x !== row.attachment_id))} />{row.display_name}</label>
      {row.phase === 'uploading' ? <div className={styles.uploadProgress}>
        <progress aria-label={`${row.display_name}上传进度`} max={row.transfer_bytes || 1}
          value={row.transfer_bytes ? Math.min(row.uploaded_bytes ?? 0, row.transfer_bytes) : undefined} />
        <small>{!row.transfer_bytes ? '正在准备文件' : (row.uploaded_bytes ?? 0) >= row.transfer_bytes ? '正在校验并登记' :
          `已确认 ${Math.floor((row.uploaded_bytes ?? 0) / row.transfer_bytes * 100)}%`}</small>
      </div> : <small>{labels[row.phase] ?? '处理中'}</small>}
      {row.phase === 'failed' && <button onClick={() => void action(row.attachment_id, 'retry')}>重试上传</button>}
      {['uploading', 'ready', 'failed'].includes(row.phase) && <button onClick={() => void action(row.attachment_id, 'cancel')}>取消附件</button>}
    </div>)}
    {!open && error && <p role="alert" className={styles.error}>{error}</p>}
    {open && <dialog ref={dialog} className={styles.resultsDialog} aria-label="选择上传文件" onCancel={e => { e.preventDefault(); close(); }} onKeyDown={e => { keepDialogFocus(e, dialog.current); if (e.key === 'Escape') { e.preventDefault(); close(); } }}>
      <header><h3>选择上传文件</h3><button onClick={close} aria-label="关闭">×</button></header>
      <div className={styles.pickerBody}>
      {locations ? <DirectoryBrowser api={api} mode="select" purpose="upload" initialPath={location?.path} close={() => showLocations(false)}
        choose={choice => { setLocation(choice); setPaths([]); setTree(undefined); showLocations(false); void browse('', 0, choice); }} /> : <>
      <p className={styles.note}>勾选文件或整个文件夹。包含隐藏文件；压缩包按原文件上传。上传完成后，随选中的提示词一起提交。</p>
      <nav className={styles.actions}><button disabled={busy || !tree?.path} onClick={() => void browse(tree!.path.split('/').slice(0, -1).join('/'))}>上一级</button><span className={styles.pickerPath}>{[location?.path, tree?.path].filter(Boolean).join(' / ') || '项目目录'}</span>
        <button disabled={busy || !!intent.current} onClick={() => showLocations(true)}>浏览此电脑</button></nav>
      <div className={styles.scopeFiles}>{tree?.entries.map(entry => <div className={styles.scopeFile} key={entry.path}>
        <label><input type="checkbox" aria-label={entry.name} disabled={busy || !!intent.current || covered(entry.path) || !['file', 'directory'].includes(entry.kind)} checked={paths.includes(entry.path) || covered(entry.path)} onChange={e => setPaths(old => e.target.checked ? [...old, entry.path] : old.filter(p => p !== entry.path))} />{entry.name}</label>
        {entry.kind === 'directory' ? <button disabled={busy} onClick={() => void browse(entry.path)}>打开 {entry.name}</button> : <small>{entry.kind === 'file' ? `${entry.size} 字节` : '不支持链接或特殊文件'}</small>}
      </div>)}</div>
      {tree?.next_offset != null && <button disabled={busy} onClick={() => void browse(tree.path, tree.next_offset!)}>加载更多</button>}
      {error && <p role="alert" className={styles.error}>{error}</p>}
      </>}
      </div>
      {!locations && <footer className={styles.actions}><span>已选 {paths.length} 项</span><button className={styles.primary} disabled={busy || !paths.length} onClick={() => void upload()}>{busy ? '处理中…' : '上传所选项'}</button></footer>}
    </dialog>}
  </div>;
}
