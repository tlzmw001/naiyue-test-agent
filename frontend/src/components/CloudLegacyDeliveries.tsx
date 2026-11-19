import { useEffect, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { LegacyDelivery } from '../cloudDeliveryTypes';
import { cloudError } from '../cloudPresentation';
import styles from './CloudTasks.module.css';

export default function CloudLegacyDeliveries({ api, sid, close }: { api: CloudApi; sid: string; close: () => void }) {
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<{ rows?: LegacyDelivery[]; error?: string }>({});
  useEffect(() => {
    const controller = new AbortController();
    setResult({});
    api.legacyDeliveries(sid, offset, controller.signal).then(rows => {
      if (!controller.signal.aborted) setResult({ rows });
    }).catch(error => {
      if (!controller.signal.aborted) setResult({ error: cloudError(error) });
    });
    return () => controller.abort();
  }, [api, sid, offset]);
  return <section className={styles.panel} aria-label="旧代码交付记录">
    <div className={styles.listHeader}><strong>旧代码交付记录</strong><button onClick={close}>返回任务</button></div>
    {result.error && <p role="alert">{result.error}</p>}
    {!result.error && !result.rows && <p role="status">正在读取旧交付记录…</p>}
    {result.rows?.length === 0 && <p>本页没有旧交付记录。</p>}
    {result.rows?.map(record => <article className={styles.resultRecord} key={record.command_id}>
      <p>{({ apply: '应用代码', branch: '创建分支', export: '导出代码' })[record.action]} · {record.recovery_required ? '待核对' : record.phase === 'completed' ? '已完成' : '已退役'}</p>
      <code>{record.command_id}</code>
      {record.path && <p>目标 <code>{record.path}</code></p>}
      {record.recovery_required && <>
        {record.done.length > 0 && <details><summary>已记录写入 {record.done.length} 项</summary><ul>{record.done.map(path => <li key={path}>{path}</li>)}</ul></details>}
        {record.evidence_path && <p>本机证据 <code>{record.evidence_path}</code></p>}
      </>}
    </article>)}
    {offset > 0 && <button onClick={() => setOffset(offset - 20)}>上一页</button>}
    {result.rows?.length === 20 && <button onClick={() => setOffset(offset + 20)}>下一页</button>}
  </section>;
}
