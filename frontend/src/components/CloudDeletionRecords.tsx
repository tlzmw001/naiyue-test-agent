import { useEffect, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudOperation } from '../cloudTypes';
import { cloudError } from '../cloudPresentation';
import styles from './CloudTasks.module.css';

const cleanupLabels: Record<string, string> = {
  pending: '待回收', running: '回收中', completed: '核对完成',
  partial: '部分回收失败', failed: '回收失败', unknown: '回收结果待核对',
};
const itemLabels: Record<string, string> = {
  pending: '待核对', retiring: '正在回收', reclaimed: '已回收',
  retained: '已保留', failed: '失败', unknown: '待核对',
};

export default function CloudDeletionRecords({ api, sid, close }: { api: CloudApi; sid: string; close: () => void }) {
  const [rows, setRows] = useState<CloudOperation[]>([]);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<string>();
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api.deletions(sid, offset, controller.signal).then(next => {
      if (!controller.signal.aborted) { setRows(next); setError(''); }
    }).catch(failure => {
      if (!controller.signal.aborted) setError(cloudError(failure));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [api, sid, offset]);

  async function check(row: CloudOperation, retry: boolean) {
    setPending(row.command_id); setError('');
    try {
      const next = await (retry ? api.retryInputCleanup(sid, row.command_id) : api.operation(sid, row.command_id));
      if (next.command_id !== row.command_id || next.binding_id !== row.binding_id) throw new Error('cloud_delete_receipt_mismatch');
      setRows(current => current.map(item => item.command_id === next.command_id ? next : item));
    } catch (failure) {
      setError(retry ? cloudError(failure) : `未能核对最新结果。${cloudError(failure)}`);
    }
    finally { setPending(undefined); }
  }

  return <section className={styles.panel} aria-label="云端删除记录">
    <div className={styles.listHeader}><strong>删除记录</strong><button onClick={close}>返回任务</button></div>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {loading ? <p role="status">正在读取删除记录…</p> : <>
      {!rows.length && <p>本页没有删除记录。</p>}
      {rows.map(row => {
        const cleanup = row.input_cleanup;
        return <article key={row.command_id} className={styles.resultRecord}>
          <p>任务 {row.task_id?.slice(0, 8)} · {row.phase === 'succeeded' ? '已删除' : row.error_code ? cloudError({ code: row.error_code }) : '删除结果待核对'}</p>
          {cleanup && <details open={['failed', 'partial', 'unknown'].includes(cleanup.state)}>
            <summary>共享输入 · {cleanupLabels[cleanup.state] ?? cleanup.state} · 已释放额度 {cleanup.released_quota_bytes} 字节</summary>
            {cleanup.reason === 'legacy_candidates_unavailable' && <p>旧删除记录没有固定候选，无法核对共享输入。</p>}
            <ul>{cleanup.candidates.map(item => {
              const reason = typeof item.reason === 'string' ? { code: item.reason } : item.reason;
              return <li key={item.input_id}><span title={item.input_id}>{item.input_id.slice(0, 12)}</span> · {itemLabels[item.state] ?? item.state}
                {reason && <> · {cloudError(reason)}{(reason.winerror ?? reason.errno) != null && <>（系统错误 {reason.winerror ?? reason.errno}）</>}</>}</li>;
            })}</ul>
          </details>}
          <div className={styles.actions}>
            <button disabled={!!pending} onClick={() => void check(row, false)}>核对结果</button>
            {row.attachment_release_pending && <p role="status">还有附件占用待释放。</p>}
            {row.phase === 'succeeded' && (row.attachment_release_pending || cleanup?.candidates.some(item => item.state !== 'reclaimed')) &&
              <button disabled={!!pending} onClick={() => void check(row, true)}>重试输入回收</button>}
          </div>
        </article>;
      })}
      {offset > 0 && <button onClick={() => setOffset(offset - 20)}>上一页</button>}
      {rows.length === 20 && <button onClick={() => setOffset(offset + 20)}>下一页</button>}
    </>}
  </section>;
}
