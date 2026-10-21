import { useEffect, useRef, useState } from 'react';
import { ApiError, type WebApi } from '../api';
import type { RunView } from '../types';
import type { DiagnosticEvent, DiagnosticEvents, DiagnosticRuns, DiagnosticSummary } from '../diagnosticTypes';
import { formatDuration } from '../observationFormat';
import styles from './SessionDiagnostics.module.css';

const labels: Record<string, string> = { started: '进行中', returned: '返回', failed: '失败',
  cancelled: '取消', unconfirmed: '未确认', completed: '已完成', stopped: '已停止',
  response_headers: '收到响应头', running: '运行中', interrupted: '已中断',
  agent: '智能体', summary: '压缩摘要', auto_fast: 'Auto 快审', auto_detailed: 'Auto 详审',
  success: '成功', error_result: '错误结果', not_executed: '未执行' };
const events: Record<string, string> = { model_call_started: '模型开始', model_call_finished: '模型结束',
  model_http_attempt_started: 'HTTP 尝试开始', model_http_attempt_finished: 'HTTP 尝试结束',
  tool_call_started: '工具开始', tool_call_finished: '工具结束', run_started: '运行开始', run_finished: '运行结束',
  compaction_started: '压缩开始', compaction_finished: '压缩结束', approval_requested: '请求审批',
  context_recovery_started: '上下文恢复开始', context_recovery_finished: '上下文恢复结束' };
const fields: Record<string, string> = { model_id: '模型', purpose: '用途', invocation_id: '模型执行',
  attempt_id: 'HTTP 尝试', tool_name: '工具', tool_call_id: '工具调用', boundary: '边界', outcome: '结果',
  error_type: '错误类型', reason: '原因', operation: '操作', trigger: '触发' };

function Summary({ value }: { value: DiagnosticSummary }) {
  const http = value.http_attempts;
  return <>
    <div className={styles.runTop}><span>{labels[value.status ?? ''] ?? (value.capture_state === 'open' ? '运行中' : '状态未提供')}</span>
      <span>耗时{formatDuration(value.duration_ms)}</span></div>
    <p className={styles.identity}>{value.run_id}</p>
    <p className={styles.meta}>{value.started_at ? new Date(value.started_at).toLocaleString() : '开始时间未提供'}</p>
    {!http || http.status === 'unavailable' ? <p className={styles.meta}>HTTP 采集不可用</p> : <>
      <p className={styles.meta}>已记录 HTTP 尝试 {http.observed_attempts} · 响应头 {http.response_headers} · 未确认 {http.unconfirmed}</p>
      {http.status === 'partial' && <p className={styles.warning}>HTTP 采集不完整</p>}
    </>}
    {value.capture_incomplete && <p className={styles.warning}>运行诊断记录不完整，已知丢失 {value.dropped_events} 条事实。</p>}
  </>;
}

function EventRow({ value }: { value: DiagnosticEvent }) {
  const data = value.data;
  return <li className={styles.event}>
    <div className={styles.runTop}><strong>#{value.sequence} {events[value.event_type] ?? value.event_type}</strong>
      {typeof data.status === 'string' && <span>{labels[data.status] ?? data.status}</span>}</div>
    <p className={styles.meta}>{value.source?.agent_path ?? '主智能体'}</p>
    {value.source && <p className={styles.identity}>{value.source.agent_run_id}</p>}
    <dl className={styles.fields}>{Object.entries(fields).map(([key, label]) =>
      typeof data[key] === 'string' || typeof data[key] === 'number' ? <div key={key}><dt>{label}</dt>
        <dd>{['purpose', 'outcome'].includes(key) ? labels[String(data[key])] ?? String(data[key]) : String(data[key])}</dd></div> : null)}
      {typeof data.http_status === 'number' && <div><dt>响应头</dt><dd>HTTP {data.http_status}</dd></div>}
      {(['duration_ms', 'response_headers_ms'] as const).map(name => name in data && <div key={name}>
        <dt>{name === 'duration_ms' ? '耗时' : '响应头耗时'}</dt>
        <dd>{formatDuration(typeof data[name] === 'number' ? data[name] : null)}</dd></div>)}
    </dl>
  </li>;
}

type Query = { sid: string; rid?: string; before?: number; after: number; revision: number };
type Result = { key: string; list?: DiagnosticRuns; detail?: DiagnosticEvents; error?: string };

export default function SessionDiagnostics({ api, sessionId, run }: { api: WebApi; sessionId: string; run: RunView | null }) {
  const [query, setQuery] = useState<Query>({ sid: sessionId, after: 0, revision: 0 });
  const current: Query = query.sid === sessionId ? query : { sid: sessionId, after: 0, revision: 0 };
  const [result, setResult] = useState<Result>();
  const finished = run?.session_id === sessionId && run.done ? run.run_id : null;
  const observed = useRef({ sid: sessionId, finished });
  useEffect(() => {
    if (observed.current.sid !== sessionId) observed.current = { sid: sessionId, finished };
    else if (finished && observed.current.finished !== finished) {
      observed.current.finished = finished;
      setQuery(old => ({ sid: sessionId, rid: old.sid === sessionId ? old.rid : undefined, after: 0,
        revision: (old.sid === sessionId ? old.revision : 0) + 1 }));
    }
  }, [sessionId, finished]);
  const key = JSON.stringify([sessionId, current.rid, current.before, current.after, current.revision]);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    async function read() {
      if (current.rid) {
        const value = await api.diagnosticEvents(sessionId, current.rid, current.after, controller.signal);
        if (value.session_id !== sessionId || value.run_id !== current.rid || value.summary.session_id !== sessionId ||
            value.summary.run_id !== current.rid || value.events.some(e => e.session_id !== sessionId || e.run_id !== current.rid))
          throw new Error('diagnostic_identity_mismatch');
        if (active) setResult({ key, detail: value });
      } else {
        const value = await api.diagnosticRuns(sessionId, current.before, controller.signal);
        if (value.session_id !== sessionId || value.runs.some(r => r.session_id !== sessionId))
          throw new Error('diagnostic_session_mismatch');
        if (active) setResult({ key, list: value });
      }
    }
    void read().catch(error => {
      if (active) setResult({ key, error: error instanceof ApiError && error.code === 'diagnostic_run_not_found'
        ? '该运行诊断不可用，可能已超出保留范围。' : '运行诊断读取失败，可以重试；当前任务不会受到影响。' });
    });
    return () => { active = false; controller.abort(); };
  }, [api, sessionId, key, current.rid, current.before, current.after]);
  const state = result?.key === key ? result : undefined;
  const refresh = () => setQuery({ sid: sessionId, rid: current.rid, after: 0, revision: current.revision + 1 });
  return <div className={styles.view}>
    {current.rid && <button className={styles.textButton} onClick={() => setQuery({ sid: sessionId, after: 0,
      revision: current.revision + 1 })}>返回运行列表</button>}
    {!state && <p className={styles.meta} role="status">正在读取运行诊断…</p>}
    {state?.error && <p className={styles.meta} role="alert">{state.error}</p>}
    {state?.list && <>
      {!state.list.runs.length && <p className={styles.meta}>暂无已保留的运行诊断。</p>}
      {state.list.runs.map(row => <button className={styles.run} key={row.run_id} aria-label={`查看运行 ${row.run_id}`}
        onClick={() => setQuery({ sid: sessionId, rid: row.run_id, after: 0, revision: current.revision + 1 })}>
        <Summary value={row} /></button>)}
      <div className={styles.pages}>
        {current.before !== undefined && <button className={styles.textButton} onClick={refresh}>返回最近运行</button>}
        {state.list.next_before !== null && <button className={styles.textButton} onClick={() => setQuery({ ...current,
          before: state.list!.next_before!, revision: current.revision + 1 })}>更早运行</button>}
      </div>
    </>}
    {state?.detail && <>
      <div className={styles.summary}><Summary value={state.detail.summary} /></div>
      <ol className={styles.events}>{state.detail.events.map(row => <EventRow value={row} key={row.sequence} />)}</ol>
      {!state.detail.events.length && <p className={styles.meta}>暂无本页事件。</p>}
      <div className={styles.pages}>
        {current.after > 0 && <button className={styles.textButton} onClick={refresh}>返回事件首页</button>}
        {state.detail.next_after !== null && <button className={styles.textButton} onClick={() => setQuery({ ...current,
          after: state.detail!.next_after!, revision: current.revision + 1 })}>下一页事件</button>}
      </div>
    </>}
    <p className={styles.note}>仅查看有限保留的运行诊断，包含子智能体、摘要和 Auto。收到响应头不代表模型执行成功。</p>
    <div className={styles.footer}><span>本地查看，不调用模型</span>
      <button className={styles.textButton} onClick={refresh} disabled={!state} aria-label="刷新运行诊断">刷新</button></div>
  </div>;
}
