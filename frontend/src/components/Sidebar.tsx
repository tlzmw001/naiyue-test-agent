import { useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import type { SessionRow } from '../types';
import { Icon } from './Icons';
import SessionMenu from './SessionMenu';
import styles from '../App.module.css';
import sidebar from './Sidebar.module.css';

export default function Sidebar({ rows, selected, open, disabled, managing, onNew, onSelect, onClose, update, remove, reorder }: {
  rows: SessionRow[]; selected?: string; open: boolean; disabled: boolean;
  onNew: () => void; onSelect: (id: string) => void; onClose: () => void;
  managing: boolean; update: (id: string, change: { title?: string; pinned?: boolean }) => Promise<boolean>;
  remove: (id: string) => Promise<boolean>; reorder: (ids: string[]) => Promise<boolean>;
}) {
  const [menu, setMenu] = useState<{ row: SessionRow; x: number; y: number } | null>(null);
  const [dragged, setDragged] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const origin = useRef<HTMLElement | null>(null);
  const newButton = useRef<HTMLButtonElement>(null);
  const gesture = useRef<{ id: string; x: number; y: number; active: boolean; ended: boolean; target: HTMLElement | null } | null>(null);
  useEffect(() => {
    if (!menu && origin.current) (origin.current.isConnected ? origin.current : newButton.current)?.focus();
  }, [menu]);
  function title(row: SessionRow) {
    return row.title ?? new Date(row.updated_at).toLocaleString('zh-CN', {
      month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  function closeMenu() {
    setMenu(null);
  }
  function openMenu(row: SessionRow, x: number, y: number, element: HTMLElement) {
    if (disabled || managing) return;
    origin.current = element;
    gesture.current = null;
    setMenu({ row, x: Math.max(8, Math.min(x, window.innerWidth - 204)), y: Math.max(8, Math.min(y, window.innerHeight - 270)) });
  }
  function ordered(id: string, target: string | null, pinned: boolean) {
    const source = rows.find(row => row.session_id === id);
    if (!source || !!source.pinned !== pinned || id === target) return Promise.resolve(false);
    const ids = rows.filter(row => row.session_id !== id).map(row => row.session_id);
    const at = target ? ids.indexOf(target) : pinned ? rows.filter(row => row.pinned && row.session_id !== id).length : ids.length;
    ids.splice(at, 0, id);
    if (ids.every((value, index) => value === rows[index].session_id)) return Promise.resolve(true);
    return reorder(ids);
  }
  const unavailable = disabled || managing;
  function pointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const current = gesture.current;
    if (!current || current.ended || unavailable) return;
    if (!current.active && Math.hypot(event.clientX - current.x, event.clientY - current.y) < 6) return;
    if (!current.active) { current.active = true; event.currentTarget.setPointerCapture?.(event.pointerId); setDragged(current.id); }
    event.preventDefault();
    current.target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>('[data-session-id], [data-session-end]') ?? null;
    setOver(current.target?.dataset.sessionId ?? null);
  }
  function pointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    const current = gesture.current;
    if (!current?.active || current.ended) { gesture.current = null; return; }
    current.ended = true; event.preventDefault();
    const target = current.target;
    if (!unavailable && target?.dataset.sessionId) {
      const row = rows.find(item => item.session_id === target.dataset.sessionId);
      if (row) void ordered(current.id, row.session_id, !!row.pinned);
    } else if (!unavailable && target?.dataset.sessionEnd) void ordered(current.id, null, target.dataset.sessionEnd === 'true');
    setDragged(null); setOver(null);
  }
  return <>
    {open && <button className={styles.backdrop} aria-label="关闭会话栏" onClick={onClose} />}
    <aside className={`${styles.sidebar} ${open ? styles.sidebarOpen : ''}`} aria-label="会话导航">
      <div className={styles.brand}><span className={styles.brandMark}>N</span><span>Naiyue</span>
        <button className={`${styles.iconButton} ${styles.mobileOnly}`} aria-label="关闭导航" onClick={onClose}><Icon name="close" /></button>
      </div>
      <button ref={newButton} className={styles.newChat} onClick={onNew} disabled={unavailable}><Icon name="plus" />新建会话</button>
      <nav className={styles.sessionList}>
        {!rows.length && <p className={styles.emptyHistory}>还没有历史会话</p>}
        {[true, false].map(pinned => {
          const group = rows.filter(row => !!row.pinned === pinned);
          if (!group.length) return null;
          return <section key={String(pinned)} role="group" aria-label={pinned ? '已置顶会话' : '普通会话'}>
            <div className={styles.sidebarLabel}>{pinned ? '已置顶' : '最近会话'}</div>
            {group.map(row => <div key={row.session_id} className={`${sidebar.row} ${over === row.session_id ? sidebar.over : ''}`}
              data-session-id={row.session_id} data-dragging={dragged === row.session_id}
              onPointerDown={event => {
                if (unavailable || event.button !== 0 || event.pointerType === 'touch' || (event.target as Element).closest('[aria-haspopup="menu"]')) return;
                gesture.current = { id: row.session_id, x: event.clientX, y: event.clientY, active: false, ended: false, target: null };
              }} onPointerMove={pointerMove} onPointerUp={pointerUp}
              onPointerCancel={() => { gesture.current = null; setDragged(null); setOver(null); }}
              onDragStart={event => event.preventDefault()}
              onClickCapture={event => { if (gesture.current?.ended) { event.preventDefault(); event.stopPropagation(); gesture.current = null; } }}
              onKeyDownCapture={() => { gesture.current = null; }}>
              <button disabled={unavailable} aria-current={selected === row.session_id ? 'true' : undefined}
                aria-label={`${title(row)} ${row.model_id}${row.state !== 'ready' ? ' · 未完成' : ''}`}
                className={`${styles.sessionButton} ${sidebar.select} ${selected === row.session_id ? styles.selected : ''}`}
                onClick={() => { setMenu(null); onSelect(row.session_id); }}
                onContextMenu={event => { if (!unavailable) { event.preventDefault(); openMenu(row, event.clientX, event.clientY, event.currentTarget); } }}
                onKeyDown={event => { if (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10') {
                  event.preventDefault(); const box = event.currentTarget.getBoundingClientRect(); openMenu(row, box.left + 24, box.bottom, event.currentTarget);
                } }}>
                <span className={styles.sessionTitle}><Icon name="chat" /><span className={sidebar.title}>{title(row)}</span></span>
                <span className={styles.sessionMeta}>{row.model_id}{row.state !== 'ready' ? ' · 未完成' : ''}</span>
              </button>
              <button type="button" className={sidebar.more} disabled={unavailable} aria-label={`${row.title ?? `未命名会话 ${row.session_id.slice(0, 8)}`}的更多操作`}
                aria-haspopup="menu" onClick={event => { const box = event.currentTarget.getBoundingClientRect(); openMenu(row, box.right - 188, box.bottom, event.currentTarget); }}>⋯</button>
            </div>)}
            {dragged && !!rows.find(row => row.session_id === dragged)?.pinned === pinned &&
              <div className={sidebar.dropEnd} data-session-end={String(pinned)}>拖到这里移至组末尾</div>}
          </section>;
        })}
      </nav>
      <div className={styles.localBadge}><span />本地工作台</div>
    </aside>
    {menu && <SessionMenu key={menu.row.session_id} row={menu.row} title={title(menu.row)} x={menu.x} y={menu.y}
      busy={managing} close={closeMenu}
      update={change => update(menu.row.session_id, change)} remove={() => remove(menu.row.session_id)} />}
  </>;
}
