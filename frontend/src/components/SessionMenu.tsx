import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { SessionRow } from '../types';
import styles from './SessionMenu.module.css';

export default function SessionMenu({ row, title, x, y, busy, close, update, remove }: {
  row: SessionRow; title: string; x: number; y: number; busy: boolean;
  close: () => void; update: (change: { title?: string; pinned?: boolean }) => Promise<boolean>;
  remove: () => Promise<boolean>;
}) {
  const [dialog, setDialog] = useState<'rename' | 'delete' | null>(null);
  const [name, setName] = useState(row.title ?? '');
  const [failed, setFailed] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const modal = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    (dialog ? modal.current : root.current)?.querySelector<HTMLElement>('input, [role="menuitem"], button')?.focus();
  }, [dialog]);
  useEffect(() => { if (dialog) modal.current?.showModal(); }, [dialog]);
  useEffect(() => {
    // 确认态由原生模态的 top layer 隔离（背景不可交互）；仅菜单态保持外部点击关闭。
    if (dialog) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) close(); };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [dialog, close]);
  async function perform(action: () => Promise<boolean>) {
    setFailed(false);
    if (await action()) close(); else setFailed(true);
  }
  return createPortal(<>
    {!dialog && <div ref={root} className={styles.menu} role="menu" aria-label="会话操作" style={{ left: x, top: y }}
      onKeyDown={event => {
        if (event.key === 'Escape' && !busy) { event.preventDefault(); close(); }
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
          const items = Array.from(root.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input') ?? []);
          const current = items.indexOf(document.activeElement as HTMLElement);
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 :
            (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
          event.preventDefault(); items[next]?.focus();
        }
      }}>
      <button type="button" role="menuitem" disabled={busy} onClick={() => setDialog('rename')}>重命名</button>
      <button type="button" role="menuitem" disabled={busy} onClick={() => void perform(() => update({ pinned: !row.pinned }))}>{row.pinned ? '取消置顶' : '置顶'}</button>
      <div className={styles.separator} />
      <button type="button" role="menuitem" className={styles.delete} disabled={busy} onClick={() => setDialog('delete')}>删除</button>
      {failed && <p role="status">操作未确认，请重试。</p>}
    </div>}
    {dialog && <dialog ref={modal} className={styles.dialog} aria-label={dialog === 'rename' ? '重命名会话' : '删除会话'}
      onCancel={event => { event.preventDefault(); if (!busy) close(); }}>
      <form onSubmit={event => { event.preventDefault(); if (!busy) void perform(dialog === 'rename' ? () => update({ title: name.trim() }) : remove); }}>
        <h2>{dialog === 'rename' ? '重命名会话' : '删除会话'}</h2>
        {dialog === 'rename' ? <label>会话名称<input aria-label="会话名称" value={name} maxLength={120} required disabled={busy}
          onChange={event => setName(event.target.value)} placeholder={title} /></label> :
          <p>确定删除“{title}”吗？会话及关联历史将永久删除，工作区文件不会删除。</p>}
        {failed && <p role="status">操作未确认，请核对提示后重试。</p>}
        <div className={styles.actions}><button type="button" disabled={busy} onClick={close}>取消</button>
          <button type="submit" className={dialog === 'delete' ? styles.danger : styles.primary}
            disabled={busy || (dialog === 'rename' && !name.trim())}>{dialog === 'rename' ? '保存名称' : '删除会话'}</button></div>
      </form>
    </dialog>}
  </>, document.body);
}
