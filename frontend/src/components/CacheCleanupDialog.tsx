import { useEffect, useRef } from 'react';
import type { useCacheCleanup } from '../useCacheCleanup';
import type { CleanupCategory, CleanupCounts, CleanupDetail } from '../cacheCleanupTypes';
import styles from './CacheCleanupDialog.module.css';

const labels: Record<CleanupCategory, string> = { tool_outputs: '工具输出归档', cloud_inputs: '云输入包缓存' };
const stops: Record<string, string> = {
  scan_budget: '已达到扫描上限。', time_budget: '已达到本次操作时限。',
  result_budget: '本次结果已达到容量上限。', service_closing: '本机服务正在关闭。',
  root_unavailable: '无法核验本机缓存范围。', internal_error: '清理中止，请查看失败明细。',
};
const errors: Record<string, string> = {
  cache_cleanup_busy: '另一次清理正在执行。',
  cache_cleanup_category_unavailable: '所选类别当前不可用，请重新读取策略。',
  cache_cleanup_result_budget: '本机服务的响应容量不足，无法开始清理。',
  request_window_full: '本机服务的请求容量已满。',
  service_instance_mismatch: '本机服务已重启，无法确认原操作结果。',
};
const outcomes = { skipped: '已跳过', failed: '失败', unconfirmed: '未确认' };
const steps: Record<string, string> = { enumerate: '枚举', read_metadata: '读取元数据', check_dependencies: '核验依赖',
  reserve: '检查占用', revalidate: '复核文件', retire: '移出缓存', unlink: '删除文件', rmdir: '移除目录',
  sync: '同步磁盘', finalize: '处理暂存残留' };

function Counts({ value }: { value: CleanupCounts }) {
  return <span>删除 {value.files_deleted} 个文件，共 {BigInt(value.bytes_deleted).toLocaleString('zh-CN')} 字节。保留 {value.units_retained}，跳过 {value.units_skipped}，失败 {value.units_failed}。</span>;
}

export default function CacheCleanupDialog({ cleanup, close }: { cleanup: ReturnType<typeof useCacheCleanup>; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    node.showModal();
    closeButton.current?.focus();
    return () => { if (node.open) node.close(); };
  }, []);
  const { operation, policies, pending, selected } = cleanup;
  const result = operation?.result;
  const groups = new Map<string, CleanupDetail[]>();
  for (const detail of result?.details ?? []) {
    const owner = detail.project_id ? `项目 ${detail.project_id}` : detail.session_id ? `会话 ${detail.session_id}` : '本机服务';
    const key = `${labels[detail.category]} · ${owner}`;
    const group = groups.get(key);
    if (group) group.push(detail);
    else groups.set(key, [detail]);
  }
  const canExecute = selected.length > 0 && selected.every(id => policies?.some(policy => policy.id === id && policy.available));
  return <dialog ref={dialog} className={styles.dialog} aria-labelledby="cache-cleanup-title" onCancel={event => { event.preventDefault(); close(); }}>
    <header className={styles.header}>
      <h2 id="cache-cleanup-title">清理缓存</h2>
      <button type="button" ref={closeButton} onClick={close}>关闭</button>
    </header>
    <p>范围为本机已登记项目与普通会话。</p>
    {cleanup.policyError ? <p role="alert">无法读取清理策略。 <button type="button" onClick={cleanup.reloadPolicies}>重新读取策略</button></p>
      : !policies ? <p role="status">正在读取清理策略…</p> : <fieldset disabled={pending}>
        <legend>选择清理类别</legend>
        {policies.map(policy => <label key={policy.id}>
          <input type="checkbox" checked={selected.includes(policy.id)} disabled={!policy.available}
            onChange={event => cleanup.setSelected(event.currentTarget.checked ? [...selected, policy.id] : selected.filter(value => value !== policy.id))} />
          <span><strong>{labels[policy.id]}</strong> · {policy.available ? `保留最近 ${policy.retention_days} 天` : '当前不可用'}<small>
            {policy.id === 'tool_outputs' ? '移除较早的详细工具输出。会话正文会保留。' : '保留正在使用及恢复所需的输入。项目原文件会保留。'}
          </small></span>
        </label>)}
      </fieldset>}
    {pending && <p role="status">{operation?.phase === 'checking' ? '正在查询原清理结果…' : '正在清理…'}</p>}
    {cleanup.storageError && <p role="alert">浏览器存储不可用，刷新后可能无法恢复查询。</p>}
    {operation?.phase === 'unknown' && <p role="alert">
      {operation.error ? errors[operation.error] ?? cleanup.unknown : cleanup.unknown}{' '}
      {cleanup.canRecheck && <button type="button" onClick={cleanup.recheck}>重新查询结果</button>}
    </p>}
    {operation?.phase === 'finished' && operation.error && <p role="alert">{errors[operation.error] ?? `清理请求失败（${operation.error}）。`}</p>}
    {result && <section aria-label="清理结果">
      <h3 role="status">{result.status === 'completed' ? '清理完成' : result.status === 'partial' ? '清理部分完成' : '清理失败'}</h3>
      <p><Counts value={result.totals} /></p>
      {result.stop_reason && <p>{stops[result.stop_reason] ?? result.stop_reason}</p>}
      <ul>{result.policies.map(({ id }) => result.categories[id] && <li key={id}>{labels[id]} · <Counts value={result.categories[id]!} /></li>)}</ul>
      {Array.from(groups, ([key, items]) => <details key={key}>
        <summary>{key} · {items.length} 条明细</summary>
        <ul>{items.map((detail, index) => <li key={index}>
          <strong>{outcomes[detail.outcome]} · {steps[detail.step] ?? detail.step}</strong>
          <div className={styles.path}>{detail.path}</div>
          <div>{detail.reason}（{detail.code}{detail.errno !== undefined && `，errno ${detail.errno}`}{detail.winerror !== undefined && `，WinError ${detail.winerror}`}）</div>
          <small>{detail.hint}</small>
        </li>)}</ul>
      </details>)}
    </section>}
    <footer className={styles.footer}><button type="button" disabled={pending || !canExecute} onClick={() => void cleanup.execute()}>清理所选</button></footer>
  </dialog>;
}
