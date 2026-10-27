import type { ImageInputs } from './ImageAttachments';
import styles from './Images.module.css';
export default function RetainedImageInputs({ inputs }: { inputs: ImageInputs }) {
  const records = inputs.pending.filter(row => row.status === 'retained' || row.status === 'uncertain');
  if (!records.length) return null;
  return <section className={styles.retained} aria-label="保留的图片输入">
    <strong>保留的图片输入</strong>
    <p>保留到本次应用关闭。恢复后由你明确发送。</p>
    {records.map(row => <div key={row.message_id} className={styles.retainedRow}>
      <span>{row.images.map(image => image.name).join('、')}</span>
      {row.status === 'uncertain' && <p className={styles.warning}>送达状态不确定，可能已经进入上下文。重新发送可能重复。</p>}
      <button type="button" disabled={inputs.reading} onClick={() => void inputs.restore(row)}>恢复到输入框</button>
      <button type="button" onClick={() => void inputs.discard(row)}>移除</button>
    </div>)}
  </section>;
}
