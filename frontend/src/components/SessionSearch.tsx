import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { SessionRow } from '../types';
import styles from './SessionSearch.module.css';

export default function SessionSearch({ rows, title, disabled, select, close, restoreTarget }: {
  rows: SessionRow[]; title: (row: SessionRow) => string; disabled: boolean;
  select: (id: string) => void; close: () => void; restoreTarget: HTMLElement | null;
}) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLElement>(null);
  const restoreFocus = useRef(true);
  const listId = useId();
  const matches = rows.filter(row => title(row).toLowerCase().includes(query.trim().toLowerCase()));
  const current = matches.find(row => row.session_id === active) ?? matches[0];
  const activeId = current && !disabled ? `${listId}-${matches.indexOf(current)}` : undefined;
  useEffect(() => {
    input.current?.focus();
    return () => { if (restoreFocus.current && restoreTarget?.isConnected) restoreTarget.focus(); };
  }, []);
  useEffect(() => {
    if (activeId) document.getElementById(activeId)?.scrollIntoView?.({ block: 'nearest' });
  }, [activeId, current?.session_id]);
  function choose(row: SessionRow) {
    if (disabled) return;
    restoreFocus.current = false;
    select(row.session_id); close();
  }
  return createPortal(<div className={styles.backdrop} onClick={event => {
    if (event.target === event.currentTarget) close();
  }}>
    <section ref={root} className={styles.dialog} role="dialog" aria-modal="true" aria-label="搜索会话"
      onKeyDown={event => {
        if (event.nativeEvent.isComposing) return;
        if (event.key === 'Escape') { event.preventDefault(); close(); return; }
        if (event.key === 'Tab') {
          const items = Array.from(root.current?.querySelectorAll<HTMLElement>('input, button:not(:disabled):not([tabindex="-1"])') ?? []);
          const first = items[0], last = items.at(-1);
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
        if (event.target !== input.current) return;
        if (['ArrowDown', 'ArrowUp'].includes(event.key) && matches.length && !disabled) {
          event.preventDefault();
          const index = matches.indexOf(current);
          setActive(matches[(index + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length].session_id);
        } else if (event.key === 'Enter') { event.preventDefault(); if (current) choose(current); }
      }}>
      <h2>搜索会话</h2>
      <div className={styles.searchBar}>
        <input ref={input} type="search" role="combobox" aria-label="搜索会话标题" placeholder="输入会话标题…"
          autoComplete="off" aria-autocomplete="list" aria-expanded="true" aria-controls={listId}
          aria-activedescendant={activeId} value={query} onChange={event => { setQuery(event.target.value); setActive(''); }} />
        <button type="button" aria-label="关闭搜索" onClick={close}>×</button>
      </div>
      <div className={styles.results} role="listbox" aria-label="匹配会话" id={listId}>
        {matches.map((row, index) => <button type="button" role="option" tabIndex={-1} key={row.session_id}
          id={`${listId}-${index}`} disabled={disabled} aria-selected={!disabled && row === current}
          className={styles.result} onClick={() => choose(row)}>
          <span className={styles.title}>{title(row)}</span>
          <span className={styles.meta}>{row.model_id}{row.pinned && <span>已置顶</span>}{row.state !== 'ready' && <span>未完成</span>}</span>
        </button>)}
      </div>
      {!matches.length && <p className={styles.empty} role="status">{rows.length ? '没有匹配的会话' : '当前没有可搜索的会话'}</p>}
      {disabled && <p className={styles.empty} role="status">当前暂不可切换会话</p>}
      <div className={styles.footer}>↑ ↓ 选择 · Enter 打开 · Esc 关闭<span>仅搜索会话标题</span></div>
    </section>
  </div>, document.body);
}
