import { useEffect, useState, useRef } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding } from '../cloudTypes';
import type { CloudResult, ResultChanges, ResultFile, ResultPreview, ResultSummary } from '../cloudResultTypes';
import { cloudError } from '../cloudPresentation';
import { backgroundTaskPollMs } from '../uiSettings';
import styles from './CloudTasks.module.css';

const phases: Record<string, string> = { waiting_terminal: '等待执行结束', pending: '等待安全保存', capturing: '正在固定文件', verifying: '正在传输核对', available: '结果已保存', failed: '结果保存失败', unknown: '保存状态待核对', abandoned: '已放弃本轮结果' };
const outcomes: Record<string, string> = { completed: '执行已结束', stopped: '执行已停止', failed: '执行失败', interrupted: '执行中断', unconfirmed: '执行结论未确认' };

export default function CloudResults({ api, sid, binding, active, changed }: { api: CloudApi; sid: string; binding: CloudBinding; active: boolean; changed: () => void }) {
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
      try { const items = await api.results(sid, binding.binding_id, offset, controller.signal); if (!controller.signal.aborted) { setRows(items); setError(''); } }
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
      if (controller.signal.aborted) return; setRecord(value);
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
  return <details className={styles.results}><summary>结果与产物{rows.length ? ` · ${rows.length}` : ''}</summary>
    <p className={styles.note}>代码改动和本轮产物由云端自动保存。下载保存到浏览器下载目录；不会覆盖本机项目。</p>
    {!rows.length && <p className={styles.note}>暂无已提交运行的结果。</p>}
    <ul className={styles.resultList}>{rows.map(row => <li key={row.result_id}><button className={styles.link} onClick={() => setSelected(row.result_id)} aria-pressed={selected === row.result_id}>
      运行 {row.source_run_id.slice(0, 8)} · {phases[row.phase] ?? row.phase}</button><small>{outcomes[row.outcome ?? '']}</small></li>)}</ul>
    <div className={styles.actions}>{offset > 0 && <button onClick={() => setOffset(v => Math.max(0, v - 20))}>上一页结果</button>}{rows.length === 20 && <button onClick={() => setOffset(v => v + 20)}>下一页结果</button>}</div>
    {record && record.plan.result_id === selected && <div className={styles.resultRecord}><p>{phases[record.phase]} · {outcomes[record.boundary?.outcome ?? ''] ?? '执行中'}</p>
      <small>结果 {record.plan.result_id} · 输入 {record.plan.input_id.slice(0, 12)}</small>
      {record.error_code && <p role="status">{cloudError({ code: record.error_code })}</p>}
      {['failed', 'pending'].includes(record.phase) && <div className={styles.actions}><button disabled={busy || Boolean(binding.cloud?.artifact_operation_id)} onClick={() => void action('retry')}>重试保存原结果</button><button disabled={busy || Boolean(binding.cloud?.artifact_operation_id)} onClick={() => setConfirm(true)}>放弃本轮结果</button></div>}
      {confirm && <p>放弃后将允许继续任务，本轮尚未保存的结果将无法取回。<button disabled={busy} onClick={() => void action('abandon')}>确认放弃</button><button onClick={() => setConfirm(false)}>取消</button></p>}
      {record.phase === 'available' && <><p className={styles.note}>文件完整性已核对；尚未执行本机应用校验，测试结论请查看报告。</p>
        {changes && <><strong>代码变化 · {changes.total}</strong><ul>{changes.changes.map(row => <li key={row.path}>{({ added: '新增', deleted: '删除', modified: '修改' } as Record<string, string>)[row.kind]} · {row.path}{!row.portable && '（Windows 路径不兼容）'}</li>)}</ul>
          {changes.next_offset !== null && <button disabled={busy} onClick={() => { setBusy(true); void api.changes(sid, binding.binding_id, record.plan.result_id, changes.next_offset!).then(next => { if (valid(record.plan.result_id)) setChanges({ ...next, changes: [...changes.changes, ...next.changes] }); }).catch(e => { if (valid(record.plan.result_id)) setError(cloudError(e)); }).finally(() => { if (valid(record.plan.result_id)) setBusy(false); }); }}>加载更多代码变化</button>}</>}
        <ul className={styles.resultList}>{record.bundle?.files.map(item => <li key={item.artifact.artifact_id}><span>{({ manifest: '输出清单', patch: '代码补丁', artifact: '产物' })[item.role]} · {item.artifact.relative_name} · {item.artifact.size} 字节</span>
          <div className={styles.actions}><button disabled={busy} onClick={() => void show(item)}>预览</button><button disabled={busy} onClick={() => { setBusy(true); void api.downloadResult(sid, binding.binding_id, record.plan.result_id, item).catch(e => { if (valid(record.plan.result_id)) setError(cloudError(e)); }).finally(() => { if (valid(record.plan.result_id)) setBusy(false); }); }}>下载</button></div></li>)}</ul>
      </>}
      {file && preview && <div><strong>{file.artifact.relative_name}</strong>{preview.binary ? <p>二进制内容，请下载查看。</p> : <pre className={styles.resultPreview}>{preview.text}</pre>}
        {!preview.binary && !preview.complete && <button disabled={busy} onClick={() => { setBusy(true); const ticket = previewTicket.current; void api.resultPreview(sid, binding.binding_id, record.plan.result_id, file.artifact.artifact_id, preview.next_offset).then(next => { if (valid(record.plan.result_id) && ticket === previewTicket.current) setPreview({ ...next, text: preview.text + next.text }); }).catch(e => { if (valid(record.plan.result_id) && ticket === previewTicket.current) setError(cloudError(e)); }).finally(() => { if (valid(record.plan.result_id) && ticket === previewTicket.current) setBusy(false); }); }}>继续预览</button>}</div>}
    </div>}{error && <p role="alert" className={styles.error}>{error}</p>}
  </details>;
}
