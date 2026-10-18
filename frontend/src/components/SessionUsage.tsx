import { useEffect, useRef, useState } from 'react';
import type { WebApi } from '../api';
import type { RunView } from '../types';
import type { SessionUsageView, UsageBucket, UsageMetric } from '../usageTypes';
import styles from './ContextPanel.module.css';

const activityLabels: Record<string, string> = { root_agent: '主智能体', child_agent: '子智能体',
  summary: '压缩摘要', auto_fast: 'Auto 快审', auto_detailed: 'Auto 详审', unattributed: '用途未归属' };
const statusLabels: Record<string, string> = { started: '进行中', returned: '返回', failed: '失败', cancelled: '取消', unconfirmed: '未确认' };
const format = (value: string | null) => value === null ? '未提供' : BigInt(value).toLocaleString('en-US');

function Metric({ label, value, cache = false }: { label: string; value: UsageMetric; cache?: boolean }) {
  return <div className={cache ? styles.cache : undefined}>
    <span>{label}{value.unknown_calls > 0 && <small>另有 {value.unknown_calls} 次调用未提供</small>}</span>
    <strong>{format(value.known_sum)}</strong>
  </div>;
}

function Breakdown({ title, rows, open = false }: { title: string; rows: (UsageBucket & { label: string })[]; open?: boolean }) {
  return rows.length > 0 && <details className={styles.usageBreakdown} open={open}>
    <summary>{title}</summary>
    {rows.map(row => <div className={styles.usageRow} key={row.label}>
      <span>{row.label}<small>{row.recorded_calls} 次调用</small></span>
      <span>输入 {format(row.metrics.input_tokens.known_sum)}<small>输出 {format(row.metrics.output_tokens.known_sum)}</small>
        {(row.metrics.input_tokens.unknown_calls > 0 || row.metrics.output_tokens.unknown_calls > 0) && <small>含未提供项</small>}</span>
    </div>)}
  </details>;
}

export default function SessionUsage({ api, sessionId, run }: { api: WebApi; sessionId: string; run: RunView | null }) {
  const [query, setQuery] = useState<{ sid: string; after?: string; revision: number }>({ sid: sessionId, revision: 0 });
  const current = query.sid === sessionId ? query : { sid: sessionId, revision: 0, after: undefined };
  const [result, setResult] = useState<{ key: string; value?: SessionUsageView; error?: string }>();
  const finished = run?.session_id === sessionId && run.done ? run.run_id : null;
  const observed = useRef({ sid: sessionId, finished });
  useEffect(() => {
    if (observed.current.sid !== sessionId) observed.current = { sid: sessionId, finished };
    else if (finished && observed.current.finished !== finished) {
      observed.current.finished = finished;
      setQuery(old => ({ sid: sessionId, revision: (old.sid === sessionId ? old.revision : 0) + 1 }));
    }
  }, [sessionId, finished]);
  const key = JSON.stringify([sessionId, current.after, current.revision]);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    void api.usage(sessionId, current.after, controller.signal).then(value => {
      if (value.session_id !== sessionId) throw new Error('usage_session_mismatch');
      if (active) setResult({ key, value });
    }).catch(() => {
      if (active) setResult({ key, error: '累计用量读取失败，可以重试；当前任务不会受到影响。' });
    });
    return () => { active = false; controller.abort(); };
  }, [api, sessionId, key, current.after]);
  const state = result?.key === key ? result : undefined;
  const value = state?.value;
  const refresh = () => setQuery(old => ({ sid: sessionId, revision: (old.sid === sessionId ? old.revision : 0) + 1 }));
  return <div className={styles.usage}>
    {!state && <p className={styles.notice} role="status">正在读取累计用量…</p>}
    {state?.error && <p className={styles.notice} role="alert">{state.error}</p>}
    {value && <>
      <p className={styles.meta}>本会话 · 包含子智能体、摘要及 Auto<br />
        截至 {new Date(value.as_of).toLocaleTimeString()} 的已提交记录</p>
      {value.recorded_calls === 0 ? <p className={styles.notice}>暂无已保存的调用用量记录。</p> : <>
        <div className={styles.metrics}>
          <Metric label="已知总输入" value={value.metrics.input_tokens} />
          <Metric label="↳ 其中缓存读取" value={value.metrics.cache_read_tokens} cache />
          <Metric label="↳ 其中缓存写入" value={value.metrics.cache_write_tokens} cache />
          <Metric label="已知输出" value={value.metrics.output_tokens} />
        </div>
        <p className={styles.meta}>已保存调用 {value.recorded_calls} 次 · {Object.entries(value.status_counts)
          .filter(([, count]) => count > 0).map(([name, count]) => `${statusLabels[name]} ${count} 次`).join(' · ')}</p>
        {value.usage_status_counts.partial > 0 && <p className={styles.notice}>包含 {value.usage_status_counts.partial} 次调用的部分用量报告。</p>}
        <Breakdown title="按用途查看" rows={value.activities.map(row => ({ ...row, label: activityLabels[row.id] }))} />
        <Breakdown title="按运行来源查看" rows={value.scopes.map(row => ({ ...row, label: row.id === 'root' ? '根运行' : '子运行' }))} />
        <Breakdown title="按模型查看" open={!!current.after} rows={value.models.items.map(row => ({ ...row, label: row.model_id }))} />
        {value.models.unattributed.recorded_calls > 0 && <Breakdown title="未归属模型" rows={[
          { ...value.models.unattributed, label: '模型身份未提供' }]} />}
        {(current.after || value.models.next_after) && <div className={styles.usagePages}>
          {current.after && <button onClick={refresh}>返回模型首页</button>}
          {value.models.next_after && <button onClick={() => setQuery({ ...current, after: value.models.next_after! })}>下一页模型</button>}
        </div>}
      </>}
      {value.capture.incomplete_runs > 0 && <p className={styles.notice} role="note">有 {value.capture.incomplete_runs} 次运行存在记录缺口；
        已知丢失 {format(value.capture.dropped_events)} 条事实。</p>}
      <div className={styles.note}><p>仅汇总已保存记录；压缩不会减少累计消耗。</p>
        <p>总输入已包含缓存分项。各分组是同一批记录的不同分类。</p>
        <p>未知用量不记为零；已保存记录不代表完整供应商账单。</p></div>
    </>}
    <div className={styles.usageFooter}><span>本地查看，不调用模型</span>
      <button onClick={refresh} disabled={!state} aria-label="刷新累计用量">刷新</button></div>
  </div>;
}
