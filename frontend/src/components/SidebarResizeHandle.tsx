import { useLayoutEffect, useRef, useState } from 'react';
import styles from '../App.module.css';

export default function SidebarResizeHandle({ side }: { side: 'navigation' | 'activity' }) {
  const handle = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointer: number; x: number; width: number } | null>(null);
  const [bounds, setBounds] = useState<{ width: number; min: number; max: number }>();
  const direction = side === 'navigation' ? 1 : -1;
  const read = () => {
    const pane = handle.current?.parentElement;
    const layout = pane?.parentElement;
    const workspace = layout?.querySelector<HTMLElement>(`.${styles.workspace}`);
    if (!pane || !layout || !workspace) return;
    const style = getComputedStyle(layout);
    const min = parseFloat(style.getPropertyValue(`--${side}-width`));
    const reserved = parseFloat(style.getPropertyValue('--conversation-width'))
      + 2 * parseFloat(style.getPropertyValue('--conversation-padding-x'))
      + (parseFloat(style.getPropertyValue('--conversation-scrollbar')) || 0);
    const width = pane.offsetWidth;
    return { layout, workspace, pane, min, width, max: width + Math.max(0, workspace.clientWidth - reserved) };
  };
  const resize = (target: number) => {
    const current = read();
    if (!current || !Number.isFinite(current.max) || !Number.isFinite(current.min)) return;
    const width = Math.max(current.min, Math.min(target, current.max));
    current.layout.style.setProperty(`--${side}-requested`, `${width - current.min}px`);
  };
  useLayoutEffect(() => {
    if (typeof ResizeObserver === 'undefined') return;
    const current = read();
    if (!current) return;
    const observer = new ResizeObserver(() => {
      const next = read();
      if (next && next.width >= next.min && Number.isFinite(next.max)) {
        setBounds(previous => previous?.width === next.width && previous?.max === next.max
          ? previous : { width: next.width, min: next.min, max: next.max });
      }
    });
    observer.observe(current.pane);
    observer.observe(current.workspace);
    const conversation = current.workspace.querySelector(`.${styles.conversation}`);
    if (conversation) observer.observe(conversation);
    return () => observer.disconnect();
  }, [side]);

  return <div ref={handle} className={`${styles.resizeHandle} ${side === 'activity' ? styles.activityResize : ''}`}
    role="separator" tabIndex={0} aria-orientation="vertical"
    aria-label={side === 'navigation' ? '调整会话导航宽度' : '调整辅助侧栏宽度'}
    aria-valuemin={bounds?.min} aria-valuemax={bounds?.max} aria-valuenow={bounds?.width}
    onPointerDown={event => {
      if (event.button !== 0 || drag.current) return;
      const current = read();
      if (!current) return;
      event.currentTarget.setPointerCapture?.(event.pointerId);
      drag.current = { pointer: event.pointerId, x: event.clientX, width: current.width };
    }}
    onPointerMove={event => {
      const start = drag.current;
      if (start && start.pointer === event.pointerId) resize(start.width + direction * (event.clientX - start.x));
    }}
    onPointerUp={() => { drag.current = null; }}
    onPointerCancel={() => { drag.current = null; }}
    onLostPointerCapture={() => { drag.current = null; }}
    onKeyDown={event => {
      const current = read();
      if (!current) return;
      const step = 16 * direction;
      const target = { Home: current.min, End: current.max, ArrowLeft: current.width - step, ArrowRight: current.width + step }[event.key];
      if (target === undefined) return;
      event.preventDefault();
      resize(target);
    }} />;
}
