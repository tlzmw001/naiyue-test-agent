import type { SessionRow } from '../types';
import { Icon } from './Icons';
import styles from '../App.module.css';

export default function Sidebar({ rows, selected, open, disabled, onNew, onSelect, onClose }: {
  rows: SessionRow[]; selected?: string; open: boolean; disabled: boolean;
  onNew: () => void; onSelect: (id: string) => void; onClose: () => void;
}) {
  return <>
    {open && <button className={styles.backdrop} aria-label="关闭会话栏" onClick={onClose} />}
    <aside className={`${styles.sidebar} ${open ? styles.sidebarOpen : ''}`} aria-label="会话导航">
      <div className={styles.brand}><span className={styles.brandMark}>N</span><span>Naiyue</span>
        <button className={`${styles.iconButton} ${styles.mobileOnly}`} aria-label="关闭导航" onClick={onClose}><Icon name="close" /></button>
      </div>
      <button className={styles.newChat} onClick={onNew} disabled={disabled}><Icon name="plus" />新建会话</button>
      <div className={styles.sidebarLabel}>最近会话</div>
      <nav className={styles.sessionList}>
        {!rows.length && <p className={styles.emptyHistory}>还没有历史会话</p>}
        {rows.map(row => <button key={row.session_id} disabled={disabled}
          className={`${styles.sessionButton} ${selected === row.session_id ? styles.selected : ''}`}
          onClick={() => onSelect(row.session_id)}>
          <span className={styles.sessionTitle}><Icon name="chat" />{new Date(row.updated_at).toLocaleString('zh-CN', {
            month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
          <span className={styles.sessionMeta}>{row.model_id}{row.state !== 'ready' ? ' · 未完成' : ''}</span>
        </button>)}
      </nav>
      <div className={styles.localBadge}><span />本地工作台</div>
    </aside>
  </>;
}
