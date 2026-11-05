import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudAttachment } from '../cloudTypes';
import type { CloudFileTree } from '../cloudTypes';
import { cloudError } from '../cloudPresentation';
import { keepDialogFocus } from '../dialogFocus';
import styles from './CloudTasks.module.css';

const labels: Record<string, string> = { uploading: '上传中', ready: '待发送', attached: '已提交', failed: '需处理', cancelled: '已取消' };
export default function CloudAttachments({ api, sid, bid, rows, selected, select, changed }: {
  api: CloudApi; sid: string; bid: string; rows: CloudAttachment[]; selected: string[];
  select: (ids: string[]) => void; changed: () => void;
}) {
  const [menu, setMenu] = useState(false); const [open, setOpen] = useState(false);
  const [tree, setTree] = useState<CloudFileTree>(); const [paths, setPaths] = useState<string[]>([]);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null); const trigger = useRef<HTMLButtonElement>(null);
  const intent = useRef<{ attachment_id: string; display_name: string; paths: string[] } | undefined>(undefined);
  const generation = useRef(0);
  useEffect(() => { if (open) { dialog.current?.showModal(); void browse(''); } return () => { generation.current++; }; }, [open]);
  function close() { dialog.current?.close(); setOpen(false); trigger.current?.focus(); }
  async function browse(path: string, offset = 0) {
    const ticket = ++generation.current; setBusy(true); setError('');
    try { const value = await api.files(sid, path, offset); if (ticket === generation.current) setTree(old => offset && old ? { ...value, entries: [...old.entries, ...value.entries] } : value); }
    catch (e) { if (ticket === generation.current) { console.warn('cloud_files_browse_failed'); setError(cloudError(e)); } }
    finally { if (ticket === generation.current) setBusy(false); }
  }
  const covered = (path: string) => paths.some(parent => path.startsWith(parent + '/'));
  async function upload() {
    if (busy || !paths.length) return; setBusy(true); setError('');
    const fixed = intent.current ?? { attachment_id: crypto.randomUUID(), display_name: paths.length === 1 ? paths[0].split('/').at(-1)! : `${paths.length} 个所选项`, paths };
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
    <button ref={trigger} aria-label="添加附件" aria-expanded={menu} onClick={() => setMenu(v => !v)}>＋</button>
    {menu && <div className={styles.actions}>{['添加文件', '添加文件夹'].map(label => <button key={label} onClick={() => { setMenu(false); setOpen(true); }}>{label}</button>)}</div>}
    {rows.filter(row => row.phase !== 'cancelled').map(row => <div key={row.attachment_id} className={styles.attachmentRow}>
      <label><input type="checkbox" checked={selected.includes(row.attachment_id)} onChange={e => select(e.target.checked ? [...selected, row.attachment_id] : selected.filter(x => x !== row.attachment_id))} />{row.display_name}</label>
      <small>{labels[row.phase] ?? '处理中'}</small>
      {row.phase === 'failed' && <button onClick={() => void action(row.attachment_id, 'retry')}>重试上传</button>}
      {['uploading', 'ready', 'failed'].includes(row.phase) && <button onClick={() => void action(row.attachment_id, 'cancel')}>取消附件</button>}
    </div>)}
    {!open && error && <p role="alert" className={styles.error}>{error}</p>}
    {open && <dialog ref={dialog} className={styles.resultsDialog} aria-label="选择上传文件" onCancel={e => { e.preventDefault(); close(); }} onKeyDown={e => { keepDialogFocus(e, dialog.current); if (e.key === 'Escape') { e.preventDefault(); close(); } }}>
      <header><h3>选择上传文件</h3><button onClick={close}>关闭</button></header>
      <p className={styles.note}>勾选文件或整个文件夹。包含隐藏文件；压缩包按原文件上传。上传完成后，随选中的提示词一起提交。</p>
      <nav className={styles.actions}><button disabled={busy || !tree?.path} onClick={() => void browse(tree!.path.split('/').slice(0, -1).join('/'))}>上一级</button><span>{tree?.path || '项目目录'}</span></nav>
      <div className={styles.scopeFiles}>{tree?.entries.map(entry => <div className={styles.scopeFile} key={entry.path}>
        <label><input type="checkbox" aria-label={entry.name} disabled={busy || !!intent.current || covered(entry.path) || !['file', 'directory'].includes(entry.kind)} checked={paths.includes(entry.path) || covered(entry.path)} onChange={e => setPaths(old => e.target.checked ? [...old, entry.path] : old.filter(p => p !== entry.path))} />{entry.name}</label>
        {entry.kind === 'directory' ? <button disabled={busy} onClick={() => void browse(entry.path)}>打开 {entry.name}</button> : <small>{entry.kind === 'file' ? `${entry.size} 字节` : '不支持链接或特殊文件'}</small>}
      </div>)}</div>
      {tree?.next_offset != null && <button disabled={busy} onClick={() => void browse(tree.path, tree.next_offset!)}>加载更多</button>}
      {error && <p role="alert" className={styles.error}>{error}</p>}
      <footer className={styles.actions}><span>已选 {paths.length} 项</span><button className={styles.primary} disabled={busy || !paths.length} onClick={() => void upload()}>{busy ? '处理中…' : '上传所选项'}</button></footer>
    </dialog>}
  </div>;
}
