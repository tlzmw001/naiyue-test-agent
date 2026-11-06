import { useEffect, useLayoutEffect, useState, useRef } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding } from '../cloudTypes';
import type { CloudResult, ResultChanges, ResultFile, ResultPreview, ResultSummary } from '../cloudResultTypes';
import { cloudError, cloudTime } from '../cloudPresentation';
import { backgroundTaskPollMs } from '../uiSettings';
import styles from './CloudTasks.module.css';
import CloudResultReceive from './CloudResultReceive';
import { keepDialogFocus } from '../dialogFocus';
import type { AnalysisDraft } from '../cloudReceiveTypes';
import { ApiError } from '../api';

const phases: Record<string, string> = { waiting_terminal: '等待执行结束', pending: '等待安全保存', capturing: '正在固定文件', verifying: '正在传输核对', available: '结果已保存', failed: '结果保存失败', unknown: '保存状态待核对', abandoned: '已放弃本轮结果', superseded: '已由后续运行替代', deleting: '正在清理结果文件', deleted: '结果文件已清理' };
const outcomes: Record<string, string> = { completed: '执行已结束', stopped: '执行已停止', failed: '执行失败', interrupted: '执行中断', unconfirmed: '执行结论未确认' };
const pageSize = 20;
const noArtifacts = (row: ResultSummary, binding: CloudBinding) =>
  row.phase === 'available' && row.files_count === 0 && (binding.cloud?.input_id ?? binding.input_id) == null;
const historical = (row: ResultSummary, binding: CloudBinding) =>
  noArtifacts(row, binding) || ['deleted', 'superseded', 'abandoned'].includes(row.phase);

type Props = { api: CloudApi; sid: string; binding: CloudBinding; active: boolean; changed: () => void; onAnalysisDraft?: (value: AnalysisDraft) => boolean | void };

export default function CloudResults(props: Props) {
  return <ResultWindow key={JSON.stringify([props.api.projectId, props.sid, props.binding.binding_id, props.binding.task_id])} {...props} />;
}

function ResultWindow({ api, sid, binding, active, changed, onAnalysisDraft }: Props) {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null); const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (open) dialog.current?.showModal(); }, [open]);
  const generation = useRef(0);
  useEffect(() => { if (!active) { generation.current++; setOpen(false); dialog.current?.close(); } }, [active]);
  function close() { generation.current++; dialog.current?.close(); setOpen(false); trigger.current?.focus(); }
  const [history, setHistory] = useState(false);
  const [rows, setRows] = useState<ResultSummary[]>(); const [offset, setOffset] = useState(0);
  const [olderAvailable, setOlderAvailable] = useState<ResultSummary>();
  const [recentCount, setRecentCount] = useState(0);
  const [selected, setSelected] = useState<string>(); const [record, setRecord] = useState<CloudResult>();
  const [changes, setChanges] = useState<ResultChanges>(); const [file, setFile] = useState<ResultFile>(); const [preview, setPreview] = useState<ResultPreview>();
  const current = useRef({ open, active, alive: true });
  current.current.open = open; current.current.active = active;
  const previewTicket = useRef(0);
  const valid = (token: number) => current.current.alive && current.current.open && current.current.active && generation.current === token;
  useLayoutEffect(() => { current.current.alive = true; return () => { current.current.alive = false; }; }, []);
  const [error, setError] = useState(''); const [listError, setListError] = useState('');
  const [busy, setBusy] = useState(false); const [confirm, setConfirm] = useState(false); const [refresh, setRefresh] = useState(0);
  const legacyInput = binding.cloud?.input_id ?? binding.input_id;
  const retainedIds = binding.cloud?.result_ids?.join(',') ?? '';
  useEffect(() => {
    if (!active || !open) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const items = await api.results(sid, binding.binding_id, offset, controller.signal);
        if (controller.signal.aborted) return;
        if (binding.task_id && items.some(item => item.task_id && item.task_id !== binding.task_id)) throw new Error('cloud_result_identity_mismatch');
        setRows(items); setListError('');
        if (!history && offset === 0) setRecentCount(items.length);
        let older: ResultSummary | undefined;
        if (!history) {
          const latest = items.find(item => item.phase === 'available' && (item.files_count !== 0 || legacyInput));
          // A page of newer historical receipts can obscure a still-retained result.
          // Inspect retained IDs from the task instead of fetching unopened history.
          if (!latest && offset === 0 && items.length === pageSize && retainedIds) {
            const onPage = new Set(items.map(item => item.result_id));
            for (const rid of retainedIds.split(',').reverse()) {
              if (onPage.has(rid)) continue;
              let detail: CloudResult;
              try { detail = await api.result(sid, binding.binding_id, rid, controller.signal); }
              catch (e) {
                if (controller.signal.aborted) return;
                if (e instanceof ApiError && e.code === 'result_not_found') continue;
                throw e;
              }
              if (controller.signal.aborted) return;
              if (detail.plan.result_id !== rid || binding.task_id && detail.plan.task_id && detail.plan.task_id !== binding.task_id)
                throw new Error('cloud_result_identity_mismatch');
              const count = detail.bundle?.files.length ?? 0;
              if (detail.phase !== 'available' || !count) continue;
              older = { result_id: rid, source_run_id: detail.plan.source_run_id, phase: detail.phase, revision: detail.revision,
                outcome: detail.boundary?.outcome ?? null, error_code: detail.error_code, captured_at: detail.bundle?.captured_at ?? null, files_count: count };
              break;
            }
          }
          const preferred = latest ?? older;
          setSelected(previous => {
            const currentRow = items.find(item => item.result_id === previous);
            if (previous && (!currentRow || !historical(currentRow, binding))) return previous;
            return preferred?.result_id;
          });
        }
        setOlderAvailable(older);
      } catch (e) { if (!controller.signal.aborted) setListError(cloudError(e)); }
      if (!controller.signal.aborted && !history && offset === 0) timer = setTimeout(() => void load(), backgroundTaskPollMs);
    }
    void load(); return () => { controller.abort(); clearTimeout(timer); };
  }, [api, sid, binding.binding_id, binding.task_id, active, open, history, offset, refresh, legacyInput, retainedIds]);
  const selectedRow = olderAvailable?.result_id === selected ? olderAvailable : rows?.find(row => row.result_id === selected);
  useLayoutEffect(() => { generation.current++; }, [selected, selectedRow?.revision]);
  function select(rid: string | undefined) {
    if (rid === selected) return;
    generation.current++; setSelected(rid); setError('');
    setRecord(undefined); setChanges(undefined); setFile(undefined); setPreview(undefined); setConfirm(false); setBusy(false);
  }
  function navigate(nextHistory: boolean, nextOffset: number) {
    select(undefined); setRows(undefined); setOlderAvailable(undefined); setListError(''); setHistory(nextHistory); setOffset(nextOffset);
  }
  useEffect(() => {
    generation.current++; previewTicket.current++;
    setBusy(false); setRecord(undefined); setChanges(undefined); setFile(undefined); setPreview(undefined); setConfirm(false);
    if (!selected || !active || !open || selectedRow && historical(selectedRow, binding) !== history) return;
    const controller = new AbortController();
    void api.result(sid, binding.binding_id, selected, controller.signal).then(async value => {
      if (controller.signal.aborted) return;
      if (value.plan.result_id !== selected || binding.task_id && value.plan.task_id && value.plan.task_id !== binding.task_id) throw new Error('cloud_result_identity_mismatch');
      setRecord(value);
      if (value.phase === 'available' && value.plan.input_id !== null) { const rows = await api.changes(sid, binding.binding_id, selected, 0, controller.signal); if (!controller.signal.aborted) setChanges(rows); }
    }).catch(e => { if (!controller.signal.aborted) setError(cloudError(e)); });
    return () => controller.abort();
  }, [api, sid, binding.binding_id, selected, active, open, refresh, history, selectedRow?.revision]);
  async function show(item: ResultFile) {
    if (!selected) return; const rid = selected; const token = generation.current; const ticket = ++previewTicket.current; setBusy(true); setError(''); setFile(item); setPreview(undefined);
    const live = () => valid(token) && ticket === previewTicket.current;
    try { const next = await api.resultPreview(sid, binding.binding_id, rid, item.artifact.artifact_id); if (live()) setPreview(next); }
    catch (e) { if (live()) setError(cloudError(e)); } finally { if (live()) setBusy(false); }
  }
  async function action(kind: 'retry' | 'abandon') {
    if (!record || !selected) return; const token = generation.current; setBusy(true); setError('');
    try { await api.resultAction(sid, binding.binding_id, selected, { command_id: crypto.randomUUID(), expected_binding_revision: binding.revision, expected_revision: record.revision, action: kind }); if (valid(token)) { setRefresh(v => v + 1); changed(); } }
    catch (e) { if (valid(token)) setError(cloudError(e)); } finally { if (valid(token)) { setBusy(false); setConfirm(false); } }
  }
  async function download(item: ResultFile, rid: string) {
    const token = generation.current;
    setBusy(true); setError('');
    try {
      const link = await api.downloadLink(sid, binding.binding_id, rid, item.artifact.artifact_id);
      if (!valid(token)) return;
      if (!/^\/api\/client\/v1\/cloud-downloads\/[0-9a-f-]{36}$/.test(link.url)) throw new Error('cloud_download_invalid');
      const anchor = document.createElement('a');
      anchor.href = link.url; anchor.download = item.artifact.relative_name.split('/').at(-1)!;
      document.body.append(anchor); anchor.click(); anchor.remove();
    } catch (e) { if (valid(token)) setError(cloudError(e)); }
    finally { if (valid(token)) setBusy(false); }
  }
  const visibleRows = rows?.filter(row => historical(row, binding) === history);
  if (olderAvailable) visibleRows?.push(olderAvailable);
  const token = generation.current;
  return <div className={styles.resultsEntry}><button ref={trigger} onClick={() => setOpen(true)}>结果与产物{recentCount ? ` · ${recentCount}` : ''}</button>
    {open && <dialog ref={dialog} className={styles.resultsDialog} aria-label={`结果与产物 · 任务 ${binding.task_id?.slice(0, 8) ?? ''}`} onKeyDown={e => { keepDialogFocus(e, dialog.current); if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } }} onCancel={e => { e.preventDefault(); close(); }}>
    <header><div><h3>结果与产物</h3><small>任务 {binding.task_id?.slice(0, 8)} · {binding.profile_id}</small></div><button aria-label="关闭结果窗口" onClick={close}>关闭</button></header><div className={styles.results}>
    <div className={styles.resultTabs} role="group" aria-label="结果视图">
      <button aria-pressed={!history} onClick={() => history && navigate(false, 0)}>可取回</button>
      <button aria-pressed={history} onClick={() => !history && navigate(true, 0)}>历史记录</button>
    </div>
    {listError && <p role="alert" className={styles.error}>{listError} <button onClick={() => setRefresh(value => value + 1)}>重试读取结果</button></p>}
    {!rows && !listError && <p role="status" className={styles.note}>正在读取结果…</p>}
    {rows?.length === 0 && !history && !offset && <p className={styles.note}>暂无已提交运行的结果。</p>}
    {visibleRows?.length === 0 && (history || !!rows?.length || !!offset) && <p className={styles.note}>{history ? '本页无历史记录。' : '本页无可取回或待处理的结果。'}</p>}
    <ul className={styles.resultList}>{visibleRows?.map(row => <li key={row.result_id}><button className={styles.link} onClick={() => select(row.result_id)} aria-pressed={selected === row.result_id}>
      运行 {row.source_run_id.slice(0, 8)} · {noArtifacts(row, binding) ? '无产物' : phases[row.phase] ?? row.phase}</button><small>{cloudTime(row.created_at ?? row.captured_at)}</small><small>{outcomes[row.outcome ?? '']}</small>
      {row.phase === 'superseded' && row.superseded_by && <small title={row.superseded_by}>由运行 {row.superseded_by.slice(0, 8)} 替代</small>}</li>)}</ul>
    <div className={styles.actions}>{offset > 0 && <button onClick={() => navigate(history, Math.max(0, offset - pageSize))}>上一页结果</button>}{rows?.length === pageSize && <button onClick={() => navigate(history, offset + pageSize)}>{history ? '加载更早记录' : '查看更早结果'}</button>}</div>
    {record && record.plan.result_id === selected && (!selectedRow || historical(selectedRow, binding) === history) && <div className={styles.resultRecord}><p>{phases[record.phase]} · {outcomes[record.boundary?.outcome ?? ''] ?? '执行中'}</p>
      <details><summary>结果标识</summary><small>结果 {record.plan.result_id}{record.plan.input_id && <> · 输入 {record.plan.input_id.slice(0, 12)}</>}</small></details>
      {record.phase === 'superseded' && <p className={styles.note}>未生成本轮固定文件{record.superseded_by ? <>，已由运行 <span title={record.superseded_by}>{record.superseded_by.slice(0, 8)}</span> 替代</> : ''}。</p>}
      {record.phase === 'deleting' && <p className={styles.note}>正在清理结果文件，暂不可下载或接收；执行结论仍保留。</p>}
      {record.phase === 'deleted' && <p className={styles.note}>结果文件已清理，执行结论仍保留。</p>}
      {record.error_code && <p role="status">{cloudError({ code: record.error_code })}</p>}
      {['failed', 'pending'].includes(record.phase) && <div className={styles.actions}><button disabled={busy || Boolean(binding.cloud?.artifact_operation_id)} onClick={() => void action('retry')}>重试保存原结果</button><button disabled={busy || Boolean(binding.cloud?.artifact_operation_id)} onClick={() => setConfirm(true)}>放弃本轮结果</button></div>}
      {confirm && <p>放弃后将允许继续任务，本轮尚未保存的结果将无法取回。<button disabled={busy} onClick={() => void action('abandon')}>确认放弃</button><button onClick={() => setConfirm(false)}>取消</button></p>}
      {record.phase === 'available' && history && <p className={styles.note}>此轮没有可取回的产物。</p>}
      {['deleted', 'deleting'].includes(record.phase) && <CloudResultReceive key={record.plan.result_id} api={api} sid={sid} bid={binding.binding_id}
        rid={record.plan.result_id} inputId={record.plan.input_id} files={[]} active={active && open} resumeOnly />}
      {record.phase === 'available' && !history && <>
        <CloudResultReceive key={`${api.projectId}:${sid}:${binding.binding_id}:${record.plan.result_id}`} api={api} sid={sid} bid={binding.binding_id}
          rid={record.plan.result_id} inputId={record.plan.input_id} files={record.bundle?.files ?? []} active={active && open} branchAvailable={record.code_branch_available}
          onAnalysisDraft={onAnalysisDraft ? value => { if (!valid(token)) return false; const accepted = onAnalysisDraft(value); if (accepted !== false) close(); return accepted; } : undefined}
          fileActions={item => <div className={styles.actions}><button disabled={busy} onClick={() => void show(item)}>预览</button>
            <button disabled={busy} onClick={() => void download(item, record.plan.result_id)}>浏览器下载</button></div>} />
        {changes && <><strong>代码变化 · {changes.total}</strong><ul>{changes.changes.map(row => <li key={row.path}>{({ added: '新增', deleted: '删除', modified: '修改' } as Record<string, string>)[row.kind]} · {row.path}{!row.portable && '（Windows 路径不兼容）'}</li>)}</ul>
          {changes.next_offset !== null && <button disabled={busy} onClick={() => { setBusy(true); void api.changes(sid, binding.binding_id, record.plan.result_id, changes.next_offset!).then(next => { if (valid(token)) setChanges({ ...next, changes: [...changes.changes, ...next.changes] }); }).catch(e => { if (valid(token)) setError(cloudError(e)); }).finally(() => { if (valid(token)) setBusy(false); }); }}>加载更多代码变化</button>}</>}
      </>}
      {record.phase === 'available' && file && preview && <div><strong>{file.artifact.relative_name}</strong>{preview.binary ? <p>二进制内容，保存到文件夹后查看。</p> : <pre className={styles.resultPreview}>{preview.text}</pre>}
        {!preview.binary && !preview.complete && <button disabled={busy} onClick={() => { setBusy(true); const ticket = previewTicket.current; void api.resultPreview(sid, binding.binding_id, record.plan.result_id, file.artifact.artifact_id, preview.next_offset).then(next => { if (valid(token) && ticket === previewTicket.current) setPreview({ ...next, text: preview.text + next.text }); }).catch(e => { if (valid(token) && ticket === previewTicket.current) setError(cloudError(e)); }).finally(() => { if (valid(token) && ticket === previewTicket.current) setBusy(false); }); }}>继续预览</button>}</div>}
    </div>}{error && <p role="alert" className={styles.error}>{error}</p>}
    </div></dialog>}
  </div>;
}
