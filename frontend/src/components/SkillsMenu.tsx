import { useEffect, useRef, useState } from 'react';
import { errorText } from '../api';
import type { WebApi } from '../api';
import type { SkillCatalogView } from '../types';
import styles from './SkillsMenu.module.css';

export default function SkillsMenu({ api, readEnabled, className, close, select }: {
  api: WebApi; readEnabled: boolean; className: string; close: () => void;
  select: (command: string, catalog: SkillCatalogView) => void;
}) {
  const [catalog, setCatalog] = useState<SkillCatalogView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const root = useRef<HTMLElement>(null);
  useEffect(() => {
    let disposed = false;
    setLoading(true); setError(''); setCatalog(null);
    api.skills().then(value => { if (!disposed) setCatalog(value); })
      .catch(error => { if (!disposed) setError(errorText(error)); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [api, revision]);
  useEffect(() => {
    if (catalog && root.current?.contains(document.activeElement))
      root.current.querySelector<HTMLButtonElement>('[data-skill-option]:not(:disabled)')?.focus();
  }, [catalog]);
  const enabled = readEnabled && catalog?.read_registered;
  return <section ref={root} role="dialog" aria-label="技能" className={`${className} ${styles.panel}`} aria-busy={loading}
    onKeyDown={event => {
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      const items = Array.from(root.current?.querySelectorAll<HTMLButtonElement>('[data-skill-option]:not(:disabled)') ?? []);
      const current = items.indexOf(document.activeElement as HTMLButtonElement);
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 :
        (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      event.preventDefault(); items[index]?.focus();
    }}>
    <div className={styles.heading}><span>技能 {catalog && <small>{catalog.entries.length}</small>}</span>
      <button type="button" aria-label="关闭技能列表" onClick={close} autoFocus>×</button></div>
    {loading ? <p className={styles.message} role="status">正在查找可用技能…</p> : error ?
      <p className={styles.message} role="status">{error}<button type="button" onClick={() => setRevision(value => value + 1)}>重试加载技能</button></p> : <>
        {!enabled && <p className={styles.message}>请先在工具中启用 read，再选择技能。</p>}
        {!catalog?.entries.length ? <p className={styles.message}>当前工作区没有可用技能。</p> :
          <div className={styles.list}>{catalog.entries.map(item => <button type="button" data-skill-option
            key={`${item.source_id}:${item.name}`} className={styles.entry} disabled={!enabled}
            onClick={() => select(item.command, catalog)}>
            <span className={styles.name}>{item.name}<small>{item.source_id === 'agents' ? '.agents' : '.claude'}</small></span>
            <span className={styles.description}>{item.description}</span><code>{item.command}</code>
          </button>)}</div>}
        {!!catalog?.diagnostics.length && <p className={styles.message} role="status">{catalog.diagnostics.length} 项技能资源未能加载，已跳过。</p>}
      </>}
    <div className={styles.footer}><p>选择后填入指令，补充任务再发送。<br />技能来自当前工作区；运行中的索引在下一轮任务更新。</p>
      <button type="button" disabled={loading} onClick={() => setRevision(value => value + 1)}>刷新技能</button></div>
  </section>;
}
