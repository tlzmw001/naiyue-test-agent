import { useState } from 'react';
import { Icon } from './Icons';
import styles from '../App.module.css';

export default function Composer({ text, pending, pendingMode, busy, stopping, sending, blocked,
  setText, setPending, send, stop }: {
  text: string; pending: string[]; pendingMode: boolean; busy: boolean; stopping: boolean;
  sending: boolean; blocked: boolean; setText: (text: string) => void;
  setPending: (values: string[]) => void; send: (followUp: boolean) => void; stop: () => void;
}) {
  const [followUp, setFollowUp] = useState(false);
  return <div className={styles.composerArea}>
    {!!pending.length && <section className={styles.pending} aria-label="待发送内容">
      <div className={styles.pendingTitle}>待发送内容 · {pending.length} 条</div>
      {pending.map((value, index) => <div key={index} className={styles.pendingItem}>
        <textarea aria-label={`待发送消息 ${index + 1}`} value={value} rows={2} onChange={event =>
          setPending(pending.map((item, i) => i === index ? event.target.value : item))} />
        <button className={styles.iconButton} aria-label={`移除待发送消息 ${index + 1}`} onClick={() =>
          setPending(pending.filter((_, i) => i !== index))}><Icon name="close" /></button>
      </div>)}
    </section>}
    <form className={styles.composer} onSubmit={event => { event.preventDefault(); send(followUp); }}>
      <textarea aria-label="消息" placeholder="描述你想完成的任务…" value={text} rows={2} maxLength={32768}
        onChange={event => setText(event.target.value)} onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault(); send(followUp);
          }
        }} />
      <div className={styles.composerActions}>
        <div>{busy && !pendingMode && <label className={styles.followUp}><input type="checkbox" checked={followUp}
          onChange={event => setFollowUp(event.target.checked)} />结束后跟进</label>}
          {pendingMode && <span className={styles.pendingHint}>{busy ? '停止期间仅保留输入' : '内容由你明确重新发送'}</span>}</div>
        <div className={styles.actionGroup}>
          {pendingMode && !busy && <span className={styles.resendLabel}>重新发送</span>}
          {busy && <button type="button" className={styles.stopButton} aria-label="停止运行" onClick={stop} disabled={stopping}>
            <Icon name="stop" /></button>}
          <button type="submit" className={styles.sendButton} aria-label={pendingMode && !busy ? '重新发送消息' : '发送消息'}
            disabled={sending || blocked || (!text.trim() && !pending.some(value => value.trim()))}><Icon name="send" /></button>
        </div>
      </div>
    </form>
    <div className={styles.composerFooter}>Enter 发送 · Shift + Enter 换行</div>
  </div>;
}
