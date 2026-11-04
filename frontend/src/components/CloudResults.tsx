import { useEffect, useState, useRef } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding } from '../cloudTypes';
import type { CloudResult, ResultChanges, ResultFile, ResultPreview, ResultSummary } from '../cloudResultTypes';
import { cloudError, cloudTime } from '../cloudPresentation';
import { backgroundTaskPollMs } from '../uiSettings';
import styles from './CloudTasks.module.css';
import CloudResultReceive from './CloudResultReceive';
import { keepDialogFocus } from '../dialogFocus';
import type { AnalysisDraft } from '../cloudReceiveTypes';

const phases: Record<string, string> = { waiting_terminal: '等待执行结束', pending: '等待安全保存', capturing: '正在固定文件', verifying: '正在传输核对', available: '结果已保存', failed: '结果保存失败', unknown: '保存状态待核对', abandoned: '已放弃本轮结果', superseded: '已由后续运行替代', deleting: '正在清理结果文件', deleted: '结果文件已清理' };
const outcomes: Record<string, string> = { completed: '执行已结束', stopped: '执行已停止', failed: '执行失败', interrupted: '执行中断', unconfirmed: '执行结论未确认' };

export default function CloudResults({ api, sid, binding, active, changed, onAnalysisDraft }: { api: CloudApi; sid: string; binding: CloudBinding; active: boolean; changed: () => void; onAnalysisDraft?: (value: AnalysisDraft) => boolean | void }) {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null); const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (open) dialog.current?.showModal(); }, [open]);
  useEffect(() => { if (!active) { setOpen(false); dialog.current?.close(); } }, [active]);
  function close() { dialog.current?.close(); setOpen(false); trigger.current?.focus(); }
  const [rows, setRows] = useState<ResultSummary[]>([]); const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string>(); const [record, setRecord] = useState<CloudResult>();
  const [changes, setChanges] = useState<ResultChanges>(); const [file, setFile] = useState<ResultFile>(); const [preview, setPreview] = useState<ResultPreview>();
  const current = useRef({ selected, binding: binding.binding_id, alive: true });
  current.current.selected = selected; current.current.binding = binding.binding_id;
  const previewTicket = useRef(0);
  const valid = (rid: string | undefined) => current.current.alive && current.current.selected === rid && current.current.binding === binding.binding_id;
  useEffect(() => { current.current.alive = true; return () => { current.current.alive = false; }; }, []);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false); const [confirm, setConfirm] = useState(false); const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try { const items = await api.results(sid, binding.binding_id, offset, controller.signal); if (!controller.signal.aborted) { if (binding.task_id && items.some(item => item.task_id && item.task_id !== binding.task_id)) throw new Error('cloud_result_identity_mismatch'); setRows(items); setError(''); } }
      catch (e) { if (!controller.signal.aborted) setError(cloudError(e)); }
      if (!controller.signal.aborted) timer = setTimeout(() => void load(), backgroundTaskPollMs);
    }
    void load(); return () => { controller.abort(); clearTimeout(timer); };
  }, [api, sid, binding.binding_id, active, offset, refresh]);
  useEffect(() => {
    previewTicket.current++; setBusy(false); setRecord(undefined); setChanges(undefined); setFile(undefined); setPreview(undefined); setConfirm(false);
    if (!selected || !active) return;
    const controller = new AbortController();
    void api.result(sid, binding.binding_id, selected, controller.signal).then(async value => {
      if (controller.signal.aborted) return;
      if (value.plan.result_id !== selected || binding.task_id && value.plan.task_id && value.plan.task_id !== binding.task_id) throw new Error('cloud_result_identity_mismatch');
      setRecord(value);
      if (value.phase === 'available') { const rows = await api.changes(sid, binding.binding_id, selected, 0, controller.signal); if (!controller.signal.aborted) setChanges(rows); }
    }).catch(e => { if (!controller.signal.aborted) setError(cloudError(e)); });
    return () => controller.abort();
  }, [api, sid, binding.binding_id, selected, active, refresh, rows.find(r => r.result_id === selected)?.revision]);
  async function show(item: ResultFile) {
    if (!selected) return; const rid = selected; const ticket = ++previewTicket.current; setBusy(true); setError(''); setFile(item); setPreview(undefined);
    const live = () => valid(rid) && ticket === previewTicket.current;
    try { const next = await api.resultPreview(sid, binding.binding_id, rid, item.artifact.artifact_id); if (live()) setPreview(next); }
    catch (e) { if (live()) setError(cloudError(e)); } finally { if (live()) setBusy(false); }
  }
  async function action(kind: 'retry' | 'abandon') {
    if (!record || !selected) return; const rid = selected; setBusy(true); setError('');
    try { await api.resultAction(sid, binding.binding_id, selected, { command_id: crypto.randomUUID(), expected_binding_revision: binding.revision, expected_revision: record.revision, action: kind }); if (valid(rid)) { setRefresh(v => v + 1); changed(); } }
    catch (e) { if (valid(rid)) setError(cloudError(e)); } finally { if (valid(rid)) { setBusy(false); setConfirm(false); } }
  }
  return <div className={styles.resultsEntry}><button ref={trigger} onClick={() => setOpen(true)}>结果与产物{rows.length ? ` · ${rows.length}` : ''}</button>
    {open && <dialog ref={dialog} className={styles.resultsDialog} aria-label={`结果与产物 · 任务 ${binding.task_id?.slice(0, 8) ?? ''}`} onKeyDown={e => { keepDialogFocus(e, dialog.current); if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } }} onCancel={e => { e.preventDefault(); close(); }}>
    <header><div><h3>结果与产物</h3><small>任务 {binding.task_id?.slice(0, 8)} · {binding.profile_id}</small></div><button aria-label="关闭结果窗口" onClick={close}>关闭</button></header><div className={styles.results}>
    {!rows.length && <p className={styles.note}>暂无已提交运行的结果。</p>}
    <ul className={styles.resultList}>{rows.map(row => <li key={row.result_id}><button className={styles.link} onClick={() => setSelected(row.result_id)} aria-pressed={selected === row.result_id}>
      运行 {row.source_run_id.slice(0, 8)} · {phases[row.phase] ?? row.phase}</button><small>{cloudTime(row.created_at ?? row.captured_at)}</small><small>{outcomes[row.outcome ?? '']}</small>
      {row.phase === 'superseded' && row.superseded_by && <small title={row.superseded_by}>由运行 {row.superseded_by.slice(0, 8)} 替代</small>}</li>)}</ul>
    <div className={styles.actions}>{offset > 0 && <button onClick={() => setOffset(v => Math.max(0, v - 20))}>上一页结果</button>}{rows.length === 20 && <button onClick={() => setOffset(v => v + 20)}>下一页结果</button>}</div>
    {record && record.plan.result_id === selected && <div className={styles.resultRecord}><p>{phases[record.phase]} · {outcomes[record.boundary?.outcome ?? ''] ?? '执行中'}</p>
      <details><summary>结果标识</summary><small>结果 {record.plan.result_id} · 输入 {record.plan.input_id.slice(0, 12)}</small></details>
      {record.phase === 'superseded' && <p className={styles.note}>未生成本轮固定文件{record.superseded_by ? <>，已由运行 <span title={record.superseded_by}>{record.superseded_by.slice(0, 8)}</span> 替代</> : ''}。</p>}
      {record.phase === 'deleting' && <p className={styles.note}>正在清理结果文件，暂不可下载或接收；执行结论仍保留。</p>}
      {record.phase === 'deleted' && <p className={styles.note}>结果文件已清理，执行结论仍保留。</p>}
      {record.error_code && <p role="status">{cloudError({ code: record.error_code })}</p>}
      {['failed', 'pending'].includes(record.phase) && <div className={styles.actions}><button disabled={busy || Boolean(binding.cloud?.artifact_operation_id)} onClick={() => void action('retry')}>重试保存原结果</button><button disabled={busy || Boolean(binding.cloud?.artifact_operation_id)} onClick={() => setConfirm(true)}>放弃本轮结果</button></div>}
      {confirm && <p>放弃后将允许继续任务，本轮尚未保存的结果将无法取回。<button disabled={busy} onClick={() => void action('abandon')}>确认放弃</button><button onClick={() => setConfirm(false)}>取消</button></p>}
      {record.phase === 'available' && <>
        <CloudResultReceive key={`${api.projectId}:${sid}:${binding.binding_id}:${record.plan.result_id}`} api={api} sid={sid} bid={binding.binding_id}
          rid={record.plan.result_id} inputId={record.plan.input_id} files={record.bundle?.files ?? []} active={active && open} branchAvailable={record.code_branch_available} onAnalysisDraft={onAnalysisDraft ? value => { const accepted = onAnalysisDraft(value); if (accepted !== false) close(); return accepted; } : undefined} />
        {changes && <><strong>代码变化 · {changes.total}</strong><ul>{changes.changes.map(row => <li key={row.path}>{({ added: '新增', deleted: '删除', modified: '修改' } as Record<string, string>)[row.kind]} · {row.path}{!row.portable && '（Windows 路径不兼容）'}</li>)}</ul>
          {changes.next_offset !== null && <button disabled={busy} onClick={() => { setBusy(true); void api.changes(sid, binding.binding_id, record.plan.result_id, changes.next_offset!).then(next => { if (valid(record.plan.result_id)) setChanges({ ...next, changes: [...changes.changes, ...next.changes] }); }).catch(e => { if (valid(record.plan.result_id)) setError(cloudError(e)); }).finally(() => { if (valid(record.plan.result_id)) setBusy(false); }); }}>加载更多代码变化</button>}</>}
        <ul className={styles.resultList}>{record.bundle?.files.filter(item => item.role === 'artifact').map(item => <li key={item.artifact.artifact_id}><span>{item.artifact.relative_name} · {item.artifact.size} 字节</span>
          <div className={styles.actions}><button disabled={busy} onClick={() => void show(item)}>预览</button></div></li>)}</ul>
      </>}
      {record.phase === 'available' && file && preview && <div><strong>{file.artifact.relative_name}</strong>{preview.binary ? <p>二进制内容，保存到文件夹后查看。</p> : <pre className={styles.resultPreview}>{preview.text}</pre>}
        {!preview.binary && !preview.complete && <button disabled={busy} onClick={() => { setBusy(true); const ticket = previewTicket.current; void api.resultPreview(sid, binding.binding_id, record.plan.result_id, file.artifact.artifact_id, preview.next_offset).then(next => { if (valid(record.plan.result_id) && ticket === previewTicket.current) setPreview({ ...next, text: preview.text + next.text }); }).catch(e => { if (valid(record.plan.result_id) && ticket === previewTicket.current) setError(cloudError(e)); }).finally(() => { if (valid(record.plan.result_id) && ticket === previewTicket.current) setBusy(false); }); }}>继续预览</button>}</div>}
    </div>}{error && <p role="alert" className={styles.error}>{error}</p>}
    </div></dialog>}
  </div>;
}
