import { useEffect, useRef, useState } from 'react';
import { themes, type Theme } from '../useTheme';
import styles from './ThemeMenu.module.css';

export default function ThemeMenu({ theme, select }: { theme: Theme; select: (value: Theme) => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  return <div className={styles.root} ref={root} onKeyDown={event => {
    if (!open) return;
    if (event.key === 'Escape') { event.preventDefault(); setOpen(false); trigger.current?.focus(); }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const items = Array.from(root.current!.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]'));
      const index = items.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 :
        (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      items[next]?.focus();
    }
  }}>
    <button type="button" className={styles.trigger} ref={trigger} aria-label={`主题：${themes[theme]}`}
      title={`主题：${themes[theme]}`} aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen(value => !value)}>
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
        <circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" />
      </svg>
    </button>
    {open && <div className={styles.menu} role="menu" aria-label="主题选择">
      {(Object.keys(themes) as Theme[]).map(value => <button type="button" role="menuitemradio" key={value}
        aria-checked={theme === value} onClick={() => { select(value); setOpen(false); trigger.current?.focus(); }}>{themes[value]}</button>)}
    </div>}
  </div>;
}
