import { useEffect, useRef, useState } from 'react';
import { ApiError, errorText, type WebApi } from '../api';
import type { DirectoryPage, FolderChoice } from '../projectTypes';
import { Chevron, Icon } from './Icons';
import { displayProjectPath } from '../projectPaths';
import styles from './ProjectPicker.module.css';

export default function DirectoryBrowser({ api, mode, initialPath, initialName = '', choose, close }: {
  api: WebApi; mode: 'select' | 'create'; initialPath?: string; initialName?: string;
  choose: (value: FolderChoice) => void; close: () => void;
}) {
  const [request, setRequest] = useState({ path: initialPath, hidden: false, revision: 0 });
  const [input, setInput] = useState(initialPath ?? '');
  const [name, setName] = useState(initialName);
  const [page, setPage] = useState<DirectoryPage>();
  const [positions, setPositions] = useState<DirectoryPage['positions']>([]);
  const [defaultError, setDefaultError] = useState('');
  const [loading, setLoading] = useState(true), [moreLoading, setMoreLoading] = useState(false);
  const [failure, setFailure] = useState('');
  const more = useRef<AbortController | undefined>(undefined);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setPage(undefined); setFailure('');
    api.browseDirectories({ path: request.path, show_hidden: request.hidden }, controller.signal).then(value => {
      if (!controller.signal.aborted) {
        setPage(value); setInput(value.path); setPositions(value.positions); setDefaultError(value.default_error ?? '');
      }
    }).catch(error => {
      if (!controller.signal.aborted) { console.warn('directory_browser_read_failed'); setFailure(errorText(error)); }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); more.current?.abort(); more.current = undefined; };
  }, [api, request]);
  function navigate(path: string) {
    setInput(path); setPage(undefined); setLoading(true); setMoreLoading(false);
    setRequest(value => ({ ...value, path, revision: value.revision + 1 }));
  }
  async function loadMore() {
    if (!page || page.next_offset === null || more.current) return;
    const captured = page, controller = new AbortController(); more.current = controller; setMoreLoading(true);
    try {
      const value = await api.browseDirectories({ path: page.path, show_hidden: request.hidden, offset: page.next_offset }, controller.signal);
      if (controller.signal.aborted) return;
      if (value.identity !== captured.identity || value.path !== captured.path) throw new ApiError('project_location_changed');
      setPage(previous => previous?.path === captured.path && previous.identity === captured.identity ? {
        ...value, items: [...new Map([...previous.items, ...value.items].map(row => [row.path, row])).values()],
      } : previous);
    } catch (error) {
      if (!controller.signal.aborted) { console.warn('directory_browser_page_failed'); setFailure(errorText(error)); }
    } finally { if (more.current === controller) { more.current = undefined; setMoreLoading(false); } }
  }
  const validName = !!name.trim() && name.length <= 255 && !['.', '..'].includes(name) && !/[\\/:\0]/.test(name) && !/[. ]$/.test(name);
  const ready = !!page && !loading && !failure && input === page.path;
  return <>
    <form className={styles.pathBar} onSubmit={event => { event.preventDefault(); if (input.trim()) navigate(input); }}>
      <button type="button" aria-label="返回上级文件夹" title="返回上级文件夹" disabled={!page?.parent || loading}
        onClick={() => page?.parent && navigate(page.parent)}>↑</button>
      <input aria-label="文件夹路径" value={input} onChange={event => setInput(event.target.value)} placeholder="输入文件夹路径" autoComplete="off" />
      <button type="submit" disabled={!input.trim()}>前往</button>
    </form>
    {defaultError && <p className={styles.hint} role="status">默认位置不可用，可选择其他位置。{errorText(new ApiError(defaultError))}</p>}
    <div className={styles.browser}>
      <nav className={styles.locations} aria-label="常用位置">{positions.map(position => <button type="button" key={position.path}
        title={position.path} onClick={() => navigate(position.path)}><Icon name="folder" /><span>{position.name}</span></button>)}</nav>
      <section className={styles.directory} aria-label="文件夹列表">
        <div className={styles.listHeading}><span>文件夹</span><label><input type="checkbox" checked={request.hidden}
          onChange={event => { setLoading(true); setRequest(value => ({ ...value, path: page?.path ?? value.path, hidden: event.target.checked })); }} />显示隐藏项</label></div>
        {loading ? <p className={styles.empty} role="status">正在读取文件夹…</p> : failure ? <p className={styles.empty} role="status">{failure}<br />
          <button type="button" onClick={() => navigate(input || request.path || '')}>重试读取目录</button></p> :
          page && !page.items.length ? <p className={styles.empty}>这个文件夹中没有子文件夹</p> :
          <div className={styles.entries} onKeyDown={event => {
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
            const rows = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[data-folder]'));
            const index = rows.indexOf(event.target as HTMLButtonElement); if (index < 0) return;
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length;
            event.preventDefault(); rows[next]?.focus();
          }}>{page?.items.map(row => <button type="button" key={row.path} data-folder aria-label={`打开文件夹 ${row.name}`}
            title={row.path} onClick={() => navigate(row.path)}><Icon name="folder" /><span>{displayProjectPath(row.name)}</span>{row.symlink && <small>链接</small>}<Chevron direction="right" /></button>)}</div>}
        {page?.next_offset !== null && page && !failure && <button className={styles.more} type="button" disabled={loading || moreLoading} onClick={() => void loadMore()}>{moreLoading ? '正在读取…' : '加载更多文件夹'}</button>}
      </section>
    </div>
    <div className={styles.folderFooter}>
      {mode === 'create' ? <label>新文件夹名称<input aria-label="新文件夹名称" value={name} maxLength={255} onChange={event => setName(event.target.value)} placeholder="输入名称" /></label> :
        <p className={styles.hint}>进入文件夹后，选择当前文件夹作为项目。</p>}
      <div className={styles.currentPath} title={page?.path} dir="ltr">{page ? `当前文件夹：${displayProjectPath(page.path)}` : '请先读取要使用的文件夹'}</div>
      <div className={styles.actions}><button type="button" onClick={close}>取消</button><button type="button" className={styles.primary}
        disabled={!ready || (mode === 'create' && !validName)} onClick={() => {
          if (page) choose({ mode, path: page.path, identity: page.identity, ...(mode === 'create' ? { folderName: name } : {}),
            target: mode === 'create' ? page.path + (page.path.endsWith(page.separator) ? '' : page.separator) + name : page.path });
        }}>{mode === 'create' ? '继续' : '选择此文件夹'}</button></div>
    </div>
  </>;
}
