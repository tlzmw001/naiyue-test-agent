import type { MaterialRef } from '../cloudReceiveTypes';
import { Icon } from './Icons';
import styles from './MaterialAttachments.module.css';

export interface MaterialInputs { current: MaterialRef[]; remove: (id: string) => void }
export default function MaterialAttachments({ inputs, busy }: { inputs: MaterialInputs; busy: boolean }) {
  if (!inputs.current.length) return null;
  return <section aria-label="云端材料引用" className={styles.materials}>
    <div className={styles.list}>{inputs.current.map(item => <div className={styles.item} key={item.material_id} title={`任务 ${item.task_id} · 结果 ${item.result_id}`}>
      <span>{item.title}<small>{item.size.toLocaleString()} 字节 · 云端上报材料</small></span>
      <button type="button" aria-label={`移除材料：${item.title}`} onClick={() => inputs.remove(item.material_id)}><Icon name="close" /></button>
    </div>)}</div>
    <p>{busy ? '本轮正在执行，材料和分析要求保留到下一轮，结束后由你发送。' : '材料正文保存在本机；确认发送后 Agent 才能读取。未发送的材料草稿保留在此浏览器标签页。'}</p>
  </section>;
}
