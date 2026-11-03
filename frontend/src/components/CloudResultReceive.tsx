import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { AnalysisDraft, ReceiveRecord } from '../cloudReceiveTypes';
import type { ResultFile } from '../cloudResultTypes';
import { cloudError } from '../cloudPresentation';
import { backgroundTaskPollMs } from '../uiSettings';
import styles from './CloudTasks.module.css';
import CloudCodeDelivery from './CloudCodeDelivery';

type Intent = { command_id: string; artifact_ids: string[] };
const phases: Record<ReceiveRecord['phase'], string> = { reserved: '本机接收已登记', downloading: '正在接收到本机', verifying: '正在核对本机材料',
  available: '本机材料已核对', failed: '本机接收失败', unknown: '本机接收状态待核对' };
export default function CloudResultReceive({ api, sid, bid, rid, inputId, files, active, onAnalysisDraft }: {
  api: CloudApi; sid: string; bid: string; rid: string; inputId: string; files: ResultFile[]; active: boolean;
  onAnalysisDraft?: (value: AnalysisDraft) => boolean | void;
}) {
  const key = `naiyue.cloud-receive.${api.projectId}.${sid}.${bid}.${rid}`;
  const [intent, setIntent] = useState<Intent | undefined>(() => {
    try { const value = JSON.parse(sessionStorage.getItem(key) ?? 'null');
      if (value && typeof value.command_id === 'string' && Array.isArray(value.artifact_ids) && value.artifact_ids.every((v: unknown) => typeof v === 'string')) return value;
    } catch { console.warn('cloud_receive_draft_read_failed'); }
  });
  const [selected, setSelected] = useState<string[]>(intent?.artifact_ids ?? []);
  const [record, setRecord] = useState<ReceiveRecord>(); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const current = useRef({ alive: true, active }); current.current.active = active;
  useEffect(() => { current.current.alive = true; return () => { current.current.alive = false; }; }, []);
  const live = () => current.current.alive;
  function checked(value: ReceiveRecord, id: string) {
    if (value.receive_id !== id || value.command_id !== id || value.local_project_id !== api.projectId || value.local_session_id !== sid ||
      value.binding_id !== bid || value.result_id !== rid || value.input_id !== inputId) throw new Error('cloud_receive_identity_mismatch');
    return value;
  }
  useEffect(() => {
    if (!active || !intent || busy || record && !record.active && ['available', 'failed', 'unknown'].includes(record.phase)) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try { const value = checked(await api.receiveStatus(sid, intent!.command_id, controller.signal), intent!.command_id);
        if (!controller.signal.aborted) { setRecord(value); setError(''); if (value.active) timer = setTimeout(() => void read(), backgroundTaskPollMs); }
      } catch (failure) { if (!controller.signal.aborted) setError(cloudError(failure)); }
    }
    void read(); return () => { controller.abort(); clearTimeout(timer); };
  }, [api, sid, bid, rid, active, intent?.command_id, busy, record?.phase, record?.active]);
  async function receive() {
    if (busy || record?.active) return;
    const same = intent && record?.phase !== 'available' && JSON.stringify(intent.artifact_ids) === JSON.stringify(selected);
    const request = same ? intent : { command_id: crypto.randomUUID(), artifact_ids: [...selected] };
    setBusy(true); setError(''); setNotice(''); setIntent(request);
    if (!same) setRecord(undefined);
    try {
      sessionStorage.setItem(key, JSON.stringify(request));
      const value = checked(await api.receive(sid, bid, rid, request), request.command_id);
      if (live()) setRecord(value);
    } catch (failure) { if (live()) setError(cloudError(failure)); }
    finally { if (live()) setBusy(false); }
  }
  async function retry() {
    if (!intent || busy) return; setBusy(true); setError('');
    try { const value = checked(await api.retryReceive(sid, intent.command_id), intent.command_id); if (live()) setRecord(value); }
    catch (failure) { if (live()) setError(cloudError(failure)); } finally { if (live()) setBusy(false); }
  }
  async function analyze() {
    if (!record || record.phase !== 'available' || busy || !current.current.active) return;
    const id = record.receive_id; setBusy(true); setError(''); setNotice('');
    try {
      const value = await api.analysisDraft(sid, id);
      if (value.local_project_id !== api.projectId || value.local_session_id !== sid || value.receive_id !== id ||
        value.material_refs.some(item => item.receive_id !== id || item.result_id !== rid)) throw new Error('cloud_receive_identity_mismatch');
      if (live() && current.current.active) {
        const accepted = onAnalysisDraft?.(value);
        setNotice(accepted === false ? '主会话已变化，请回到来源会话后重新加入。' : '已放入主会话输入框，请确认后发送。');
      }
    } catch (failure) { if (live()) setError(cloudError(failure)); } finally { if (live()) setBusy(false); }
  }
  const locked = busy || record?.active === true;
  return <section aria-label="本机接收材料">
    <p className={styles.note}>输出清单和代码补丁会一并接收。选择需要分析的报告或交接材料，文件保存在本机管理目录。</p>
    {files.filter(item => item.role === 'artifact').map(item => <label key={item.artifact.artifact_id} className={styles.check}>
      <input type="checkbox" disabled={locked || !!intent && !record} checked={selected.includes(item.artifact.artifact_id)} onChange={event => {
        const id = item.artifact.artifact_id; setSelected(old => event.target.checked ? [...old, id] : old.filter(value => value !== id));
      }} />{item.artifact.relative_name} · {item.artifact.size} 字节</label>)}
    <div className={styles.actions}><button disabled={locked} onClick={() => void receive()}>接收到本机</button>
      {record && !record.active && record.phase !== 'available' && <button disabled={busy} onClick={() => void retry()}>继续原接收</button>}
      {record?.phase === 'available' && onAnalysisDraft && <button disabled={busy || !active} onClick={() => void analyze()}>交给主会话分析</button>}</div>
    {record && <p className={styles.note} role="status">{phases[record.phase]}{record.files.length > 0 && ` · ${record.files.reduce((n, f) => n + f.offset, 0)} / ${record.files.reduce((n, f) => n + f.artifact.size, 0)} 字节`}</p>}
    {notice && <p className={styles.note} role="status">{notice}</p>}{error && <p role="alert" className={styles.error}>{error}</p>}
    {record?.error_code && <p className={styles.error}>{cloudError({ code: record.error_code })}</p>}
    {record?.phase === 'available' && <CloudCodeDelivery key={record.receive_id} api={api} sid={sid} receiveId={record.receive_id} resultKey={`${bid}.${rid}`} active={active} />}
  </section>;
}
