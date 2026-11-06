import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ApiError } from '../api';
import type { CloudApi } from '../cloudApi';
import type { ReceiveRecord } from '../cloudReceiveTypes';
import type { DeliveryRequest, DeliveryRecord } from '../cloudDeliveryTypes';
import type { FolderChoice } from '../projectTypes';
import { cloudError } from '../cloudPresentation';
import { backgroundTaskPollMs } from '../uiSettings';
import DirectoryBrowser from './DirectoryBrowser';
import styles from './CloudTasks.module.css';
import deliveryStyles from './CloudCodeDelivery.module.css';

type Request = Omit<DeliveryRequest, 'local_session_id'>;
export default function CloudFilesDelivery({ api, sid, resultKey, active, selected, onSelectionLock, prepareMaterials, resumeOnly = false }: {
  api: CloudApi; sid: string; resultKey: string; active: boolean; selected: string[]; onSelectionLock: (locked: boolean) => void; prepareMaterials: (refresh?: boolean, fixedId?: string) => Promise<ReceiveRecord>;
  resumeOnly?: boolean;
}) {
  const key = `naiyue.cloud-files.${api.projectId}.${sid}.${resultKey}`;
  const [request, setRequest] = useState<Request | undefined>(() => {
    try { const value = JSON.parse(sessionStorage.getItem(key) ?? 'null');
      if (value?.action === 'files' && value.destination_handle === api.projectId && typeof value.command_id === 'string' && typeof value.receive_id === 'string') return value;
    } catch { console.warn('cloud_files_draft_read_failed'); }
  });
  useLayoutEffect(() => { onSelectionLock(!!request); return () => onSelectionLock(false); }, [!!request]);
  const [browser, setBrowser] = useState<'select' | 'create'>(); const [record, setRecord] = useState<DeliveryRecord>();
  const [choice, setChoice] = useState<FolderChoice>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(''); const [unconfirmed, setUnconfirmed] = useState(false);
  const [statusFailed, setStatusFailed] = useState(false);
  const autoSave = useRef<string>(undefined);
  const alive = useRef(true); useLayoutEffect(() => { alive.current = true; return () => { alive.current = false; autoSave.current = undefined; }; }, []);
  function checked(value: DeliveryRecord, fixed: Request) {
    if (value.command_id !== fixed.command_id || value.receive_id !== fixed.receive_id || value.local_project_id !== api.projectId ||
      value.local_session_id !== sid || value.action !== 'files' || value.name !== fixed.name) throw new Error('cloud_delivery_identity_mismatch');
    return value;
  }
  function adopt(value: DeliveryRecord) {
    if (!['preparing', 'prepared'].includes(value.phase)) autoSave.current = undefined;
    setRecord(value); setUnconfirmed(false); setStatusFailed(false);
  }
  useEffect(() => {
    if (!active || !request || busy || record && !['preparing', 'executing'].includes(record.phase)) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try { const value = checked(await api.deliveryStatus(sid, request!.command_id, controller.signal), request!);
        if (!controller.signal.aborted) { adopt(value); setError(''); if (['preparing', 'executing'].includes(value.phase)) timer = setTimeout(() => void read(), backgroundTaskPollMs); }
      } catch (failure) { if (!controller.signal.aborted) {
        autoSave.current = undefined;
        setStatusFailed(true);
        if (record || !(failure instanceof ApiError && failure.code === 'cloud_delivery_not_found')) setError(cloudError(failure));
      } }
    }
    void read(); return () => { controller.abort(); clearTimeout(timer); };
  }, [api, sid, active, request?.command_id, busy, record?.phase]);
  function reset() {
    autoSave.current = undefined;
    try { sessionStorage.removeItem(key); onSelectionLock(false); setRequest(undefined); setRecord(undefined); setUnconfirmed(false); setStatusFailed(false); setError(''); }
    catch { console.warn('cloud_files_intent_clear_failed'); setError('浏览器未能清除原保存意图，请保留原操作核对。'); }
  }
  async function prepare() {
    if (busy || !active || !request && (!choice || !selected.length)) return;
    setBusy(true); setError(''); setBrowser(undefined);
    try {
      const received = request ? undefined : await prepareMaterials();
      if (!alive.current) return;
      const fixed = request ?? { command_id: crypto.randomUUID(), receive_id: received!.receive_id, action: 'files' as const,
        destination_handle: api.projectId, destination_path: choice!.path, expected_directory: choice!.identity, name: choice!.folderName ?? '' };
      sessionStorage.setItem(key, JSON.stringify(fixed)); onSelectionLock(true); setRequest(fixed);
      if (!request) autoSave.current = fixed.command_id;
      const value = checked(await api.delivery(sid, fixed), fixed); if (alive.current) adopt(value);
    } catch (failure) { if (alive.current) {
      autoSave.current = undefined;
      if (!request && failure instanceof ApiError && ['invalid_request', 'cloud_delivery_name_invalid', 'cloud_delivery_destination_forbidden', 'cloud_delivery_root_changed', 'cloud_delivery_receive_unavailable'].includes(failure.code)) reset();
      setError(cloudError(failure));
    } } finally { if (alive.current) setBusy(false); }
  }
  async function update(kind: 'query' | 'recheck' | 'execute', force = false) {
    if (!request || busy || !active) return; setBusy(true); setError('');
    autoSave.current = undefined;
    if (kind === 'execute') setUnconfirmed(true);
    try {
      const value = kind === 'execute' ? await api.executeDelivery(sid, request.command_id, force) :
        kind === 'recheck' ? await api.recheckDelivery(sid, request.command_id) : await api.deliveryStatus(sid, request.command_id);
      if (alive.current) adopt(checked(value, request));
    } catch (failure) { if (alive.current) setError(cloudError(failure)); } finally { if (alive.current) setBusy(false); }
  }
  useEffect(() => {
    if (!active) { autoSave.current = undefined; return; }
    if (!busy && request && record?.phase === 'prepared' && autoSave.current === request.command_id) {
      autoSave.current = undefined;
      if (!record.conflicts.length) void update('execute');
    }
  }, [active, busy, request, record]);
  const pending = busy || !active || ['preparing', 'executing'].includes(record?.phase ?? '');
  if (resumeOnly && !request) return null;
  return <section className={deliveryStyles.delivery} aria-label="保存报告">
    {!request && <div className={styles.actions}>
      {choice && <span className={styles.note}>保存到：{choice.target}</span>}
      <button disabled={pending || !selected.length} onClick={() => setBrowser('select')}>选择文件夹</button>
      <button className={styles.resultPrimary} disabled={pending || !!browser || !choice || !selected.length} onClick={() => void prepare()}>保存到本地</button>
    </div>}
    {browser && <div className={deliveryStyles.browser}><div className={styles.actions}><button onClick={() => setBrowser('select')} aria-pressed={browser === 'select'}>使用已有文件夹</button><button onClick={() => setBrowser('create')} aria-pressed={browser === 'create'}>新建文件夹</button></div>
      <DirectoryBrowser api={api} purpose="save" mode={browser} choose={value => { setChoice(value); setBrowser(undefined); }} close={() => setBrowser(undefined)} /></div>}
    {request && <><p className={styles.note}>保存到：{record?.destination_path ?? [request.destination_path, request.name].filter(Boolean).join('/')}</p>
      <div className={styles.actions}>{!record && <button disabled={pending} onClick={() => void prepare()}>核对原保存请求</button>}
        <button disabled={busy || !active || pending && !statusFailed} onClick={() => void update('query')}>查询保存状态</button>
        {record && ['prepared', 'failed', 'partial', 'unknown'].includes(record.phase) && <button disabled={pending || unconfirmed} onClick={() => void update('recheck')}>重新预检保存</button>}
        {!resumeOnly && record && ['prepared', 'failed', 'completed'].includes(record.phase) && <button disabled={pending || unconfirmed} onClick={reset}>更换保存位置或选择</button>}</div></>}
    {record && <><p role="status" className={styles.note}>{({ preparing: '正在预检保存位置', prepared: '保存预检已完成', executing: '正在保存文件', completed: '文件已保存到所选文件夹', partial: '保存尚未完成，请核对原进度', failed: '保存未完成', unknown: '保存状态待核对' })[record.phase]}{record.done?.length ? ` · 已保存 ${record.done.length} 个文件` : ''}</p>
      {!!record.conflicts.length && <div role="alert" className={styles.error}>同名文件内容不同：<ul>{record.conflicts.map(path => <li key={path}>{path}</li>)}</ul></div>}
      {['prepared', 'partial', 'unknown'].includes(record.phase) && !unconfirmed && <div className={styles.actions}>
        {record.phase === 'prepared' && record.conflicts.length > 0
          ? <button className={styles.resultPrimary} disabled={pending || !record.force_allowed} onClick={() => void update('execute', true)}>确认覆盖并保存</button>
          : <button disabled={pending || autoSave.current === request?.command_id} onClick={() => void update('execute', record.force ?? false)}>继续原保存</button>}</div>}
      {record.error_code && <p className={styles.error}>{cloudError({ code: record.error_code })}</p>}
      {!resumeOnly && record.error_code === 'cloud_material_unavailable' && <button disabled={pending} onClick={() => { setBusy(true); void prepareMaterials(true, request?.receive_id).then(value => { if (value.receive_id !== request?.receive_id) throw new Error('cloud_delivery_identity_mismatch'); setError('材料已重新核对，请继续原保存。'); }).catch(e => setError(cloudError(e))).finally(() => { if (alive.current) setBusy(false); }); }}>重新核对报告材料</button>}</>}
    {unconfirmed && <p role="status" className={styles.note}>原保存结果未确认，请查询保存状态后继续。</p>}
    {error && <p role="alert" className={styles.error}>{error}</p>}
  </section>;
}
