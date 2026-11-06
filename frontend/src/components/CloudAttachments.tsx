import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudAttachment } from '../cloudTypes';
import type { CloudFileTree } from '../cloudTypes';
import { cloudError } from '../cloudPresentation';
import { keepDialogFocus } from '../dialogFocus';
import type { DirectoryPage } from '../projectTypes';
import picker from './ProjectPicker.module.css';
import { Icon } from './Icons';
import composer from './ComposerControls.module.css';
import styles from './CloudTasks.module.css';

const labels: Record<string, string> = { uploading: '上传中', ready: '待发送', attached: '已提交', failed: '上传失败', cancelled: '已取消' };
type Location = Pick<DirectoryPage, 'path' | 'identity' | 'parent' | 'separator'>;
type Selection = { location?: Location; paths: string[] };
type Upload = Parameters<CloudApi['uploadFiles']>[2];

const sourceKey = (location?: Pick<Location, 'path' | 'identity'>) => location ? JSON.stringify([location.path, location.identity]) : '';
function hasSelectedParent(path: string, selected: ReadonlySet<string>) {
  let separator = path.lastIndexOf('/');
  while (separator > 0) {
    if (selected.has(path.slice(0, separator))) return true;
    separator = path.lastIndexOf('/', separator - 1);
  }
  return false;
}

export default function CloudAttachments({ api, sid, bid, rows, selected, select, changed }: {
  api: CloudApi; sid: string; bid: string; rows: CloudAttachment[]; selected: string[];
  select: (ids: string[]) => void; changed: () => void;
}) {
  const [menu, setMenu] = useState(false); const [open, setOpen] = useState(false);
  const [location, setLocation] = useState<Location>();
  const [positions, setPositions] = useState<DirectoryPage['positions']>([]);
  const [address, setAddress] = useState('');
  const [tree, setTree] = useState<CloudFileTree>();
  const [selections, setSelections] = useState<Record<string, Selection>>({});
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null); const trigger = useRef<HTMLButtonElement>(null);
  const intent = useRef<Upload[]>([]);
  const generation = useRef(0);
  useEffect(() => {
    if (!open) return;
    dialog.current?.showModal();
    const controller = new AbortController();
    void browse(tree?.path ?? '', 0, location);
    void api.browseDirectories({}, controller.signal).then(page => {
      if (!controller.signal.aborted) setPositions(page.positions);
    }).catch(() => { if (!controller.signal.aborted) console.warn('cloud_attachment_positions_failed'); });
    return () => { controller.abort(); generation.current++; };
  }, [open]);
  function close() { dialog.current?.close(); setOpen(false); trigger.current?.focus(); }
  async function browse(path: string, offset = 0, source: Location | null | undefined = location, absolute?: string) {
    const ticket = ++generation.current; setBusy(true); setError('');
    try {
      const directory = absolute === undefined ? source ?? undefined : await api.browseDirectories({ path: absolute });
      if (ticket !== generation.current) return;
      const value = await api.files(sid, path, offset, undefined, directory);
      if (ticket !== generation.current) return;
      setLocation(directory);
      if (directory) {
        const base = directory.path.endsWith(directory.separator) ? directory.path : directory.path + directory.separator;
        setAddress(value.path ? base + value.path.replaceAll('/', directory.separator) : directory.path);
      } else setAddress('');
      setTree(old => offset && old ? { ...value, entries: [...old.entries, ...value.entries] } : value);
    } catch (e) {
      if (ticket === generation.current) { console.warn('cloud_files_browse_failed'); setError(cloudError(e)); }
    } finally { if (ticket === generation.current) setBusy(false); }
  }
  const key = sourceKey(location);
  const paths = selections[key]?.paths ?? [];
  const selectedPaths = new Set(paths);
  const covered = (path: string) => hasSelectedParent(path, selectedPaths);
  const count = Object.values(selections).reduce((total, batch) => total + batch.paths.length, 0);
  const locked = intent.current.length > 0;
  function toggle(targets: string[], checked: boolean) {
    setSelections(previous => {
      const current = previous[key]?.paths ?? [];
      const incoming = new Set(targets);
      const next = new Set(checked ? [...current, ...targets] : current.filter(path => !incoming.has(path)));
      return { ...previous, [key]: { location, paths: [...next].filter(path => !hasSelectedParent(path, next)) } };
    });
  }
  async function upload() {
    if (busy || !count) return;
    setBusy(true); setError('');
    if (!intent.current.length) {
      intent.current = Object.values(selections).filter(batch => batch.paths.length).map(({ location, paths }) => ({
        attachment_id: crypto.randomUUID(),
        display_name: paths.length === 1 ? paths[0].split('/').at(-1)! : `${paths.length} 个所选项`,
        paths,
        ...(location && { directory: location.path, directory_identity: location.identity }),
      }));
    }
    const registered: string[] = [];
    try {
      while (intent.current.length) {
        const fixed = intent.current[0];
        const item = await api.uploadFiles(sid, bid, fixed);
        registered.push(item.attachment_id);
        intent.current.shift();
        const finishedKey = sourceKey(fixed.directory ? { path: fixed.directory, identity: fixed.directory_identity! } : undefined);
        setSelections(previous => {
          const next = { ...previous }; delete next[finishedKey]; return next;
        });
      }
      close();
    } catch (e) {
      console.warn('cloud_files_upload_failed'); setError(cloudError(e));
    } finally {
      if (registered.length) { select([...new Set([...selected, ...registered])]); changed(); }
      setBusy(false);
    }
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
        <button className={composer.command} onClick={() => { setMenu(false); setOpen(true); }}>添加文件或文件夹</button>
      </div>}
    </div>
    {rows.filter(row => row.phase !== 'cancelled' && (row.phase !== 'attached' || selected.includes(row.attachment_id))).map(row => <div key={row.attachment_id} className={styles.attachmentRow}>
      <label><input type="checkbox" checked={selected.includes(row.attachment_id)} onChange={e => select(e.target.checked ? [...selected, row.attachment_id] : selected.filter(x => x !== row.attachment_id))} />{row.display_name}</label>
      {row.phase === 'uploading' ? <div className={styles.uploadProgress}>
        <progress aria-label={`${row.display_name}上传进度`} max={row.transfer_bytes || 1}
          value={row.transfer_bytes ? Math.min(row.uploaded_bytes ?? 0, row.transfer_bytes) : undefined} />
        <small>{!row.transfer_bytes ? '正在准备文件' : (row.uploaded_bytes ?? 0) >= row.transfer_bytes ? '正在校验并登记' :
          `已确认 ${Math.floor((row.uploaded_bytes ?? 0) / row.transfer_bytes * 100)}%`}</small>
      </div> : <small>{labels[row.phase] ?? '处理中'}</small>}
      {row.phase === 'failed' && <>
        <p role="alert" className={`${styles.error} ${styles.attachmentError}`}>
          {cloudError({ code: row.error_code ?? 'attachment_upload_failed' })}
        </p>
        <button onClick={() => void action(row.attachment_id, 'retry')}>重试上传</button>
      </>}
      {['uploading', 'ready', 'failed'].includes(row.phase) && <button onClick={() => void action(row.attachment_id, 'cancel')}>取消附件</button>}
    </div>)}
    {!open && error && <p role="alert" className={styles.error}>{error}</p>}
    {open && <dialog ref={dialog} className={styles.resultsDialog} aria-label="选择上传文件"
      onCancel={e => { e.preventDefault(); if (!busy || !locked) close(); }}
      onKeyDown={e => { keepDialogFocus(e, dialog.current); if (e.key === 'Escape') { e.preventDefault(); if (!busy || !locked) close(); } }}>
      <header><h3>选择上传文件</h3><button onClick={close} disabled={busy && locked} aria-label="关闭">×</button></header>
      <div className={styles.pickerBody}>
        <form className={picker.pathBar} onSubmit={e => { e.preventDefault(); if (address.trim()) void browse('', 0, undefined, address.trim()); }}>
          <button type="button" disabled={busy || locked || (!tree?.path && !location?.parent)}
            onClick={() => tree?.path ? void browse(tree.path.split('/').slice(0, -1).join('/')) : void browse('', 0, undefined, location!.parent!)}>上一级</button>
          <input aria-label="文件夹路径" placeholder="输入本机文件夹路径" value={address} disabled={busy || locked}
            onChange={e => setAddress(e.target.value)} />
          <button type="submit" disabled={busy || locked || !address.trim()}>前往</button>
        </form>
        <div className={picker.browser}>
          <nav className={picker.locations} aria-label="本机位置">
            <button disabled={busy || locked} onClick={() => void browse('', 0, null)}>项目目录</button>
            {positions.map(position => <button key={position.path} title={position.path} disabled={busy || locked}
              onClick={() => void browse('', 0, undefined, position.path)}>{position.name}</button>)}
          </nav>
          <section className={picker.directory} aria-label="文件与文件夹">
            <p className={styles.note}>勾选文件或整个文件夹。点击文件夹名称可进入。压缩包按原文件上传。</p>
            <div className={styles.scopeNavigation}>
              <span className={styles.pickerPath}>{location?.path ?? '项目目录'}{tree?.path ? ` / ${tree.path}` : ''}</span>
              <button disabled={busy || locked || !tree?.entries.some(e => ['file', 'directory'].includes(e.kind) && !covered(e.path) && !selectedPaths.has(e.path))}
                onClick={() => toggle(tree!.entries.filter(e => ['file', 'directory'].includes(e.kind) && !covered(e.path)).map(e => e.path), true)}>全选已加载项</button>
            </div>
            {!tree?.entries.length && <p role="status">{busy ? '正在读取文件…' : '此处没有可显示的文件'}</p>}
            <div className={styles.scopeFiles}>{tree?.entries.map(entry => <div className={styles.scopeFile} key={entry.path}>
              <label><input type="checkbox" aria-label={entry.name} disabled={busy || locked || covered(entry.path) || !['file', 'directory'].includes(entry.kind)}
                checked={selectedPaths.has(entry.path) || covered(entry.path)} onChange={e => toggle([entry.path], e.target.checked)} />
                {entry.kind !== 'directory' && entry.name}</label>
              {entry.kind === 'directory' ? <button className={styles.pickerFolder} disabled={busy || locked}
                aria-label={`打开 ${entry.name}`} onClick={() => void browse(entry.path)}><Icon name="folder" /><span>{entry.name}</span></button> :
                <small>{entry.kind === 'file' ? `${entry.size} 字节` : '不支持链接或特殊文件'}</small>}
            </div>)}</div>
            {tree?.next_offset != null && <button disabled={busy || locked} onClick={() => void browse(tree.path, tree.next_offset!)}>加载更多</button>}
          </section>
        </div>
        {error && <p role="alert" className={styles.error}>{error}</p>}
        {!!count && <button type="button" className={styles.clearAttachmentSelection} disabled={busy} onClick={() => {
          setSelections({});
          if (locked) {
            intent.current = [];
            setError('');
            changed();
          }
        }}>{locked ? '放弃待确认选择' : '清空所选项'}</button>}
      </div>
      <footer className={styles.actions}>
        <span>已选 {count} 项</span>
        <button className={styles.primary} disabled={busy || !count} onClick={() => void upload()}>{busy ? '处理中…' : '上传所选项'}</button>
      </footer>
    </dialog>}
  </div>;
}
