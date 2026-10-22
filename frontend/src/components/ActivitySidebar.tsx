import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { Icon } from './Icons';
import styles from './ActivitySidebar.module.css';

const tabs = [{ id: 'agents', label: '子智能体' }, { id: 'tasks', label: '后台任务' }, { id: 'context', label: '上下文' }] as const;
export type ActivityTab = (typeof tabs)[number]['id'];

export default function ActivitySidebar({ tab, select, close, activeCount, children }: {
  tab: ActivityTab; select: (tab: ActivityTab) => void; close: () => void;
  activeCount?: number; children: Record<ActivityTab, ReactNode>;
}) {
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => { closeButton.current?.focus(); }, []);
  useEffect(() => {
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape' && !event.defaultPrevented) close(); };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, [close]);
  return <aside className={styles.panel} aria-label="辅助侧栏" id="activity-sidebar">
    <header className={styles.header}>
      <div className={styles.tabs} role="tablist" aria-label="辅助功能">{tabs.map((item, index) =>
      <button role="tab" key={item.id} id={`activity-tab-${item.id}`} aria-controls={`activity-${item.id}`}
        aria-selected={tab === item.id} tabIndex={tab === item.id ? 0 : -1} onClick={() => select(item.id)}
        onKeyDown={event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const next = tabs[event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 :
            (index + (event.key === 'ArrowLeft' ? tabs.length - 1 : 1)) % tabs.length];
          select(next.id);
          event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`#activity-tab-${next.id}`)?.focus();
        }}>{item.label}{item.id === 'tasks' && <span>{activeCount ?? '?'}</span>}</button>)}</div>
      <button ref={closeButton} className={styles.close} aria-label="收起辅助侧栏" onClick={close}><Icon name="close" /></button>
    </header>
    {tabs.map(item => <div className={styles.content} key={item.id} role="tabpanel" hidden={tab !== item.id}
      id={`activity-${item.id}`} aria-labelledby={`activity-tab-${item.id}`}>{children[item.id]}</div>)}
  </aside>;
}
