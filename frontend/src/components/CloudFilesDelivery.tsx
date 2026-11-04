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
export default function CloudFilesDelivery({ api, sid, resultKey, active, selected, onSelectionLock, prepareMaterials }: {
  api: CloudApi; sid: string; resultKey: string; active: boolean; selected: string[]; onSelectionLock: (locked: boolean) => void; prepareMaterials: (refresh?: boolean, fixedId?: string) => Promise<ReceiveRecord>;
}) {
  const key = `naiyue.cloud-files.${api.projectId}.${sid}.${resultKey}`;
  const [request, setRequest] = useState<Request | undefined>(() => {
    try { const value = JSON.parse(sessionStorage.getItem(key) ?? 'null');
      if (value?.action === 'files' && value.destination_handle === api.projectId && typeof value.command_id === 'string' && typeof value.receive_id === 'string') return value;
    } catch { console.warn('cloud_files_draft_read_failed'); }
  });
  useLayoutEffect(() => { onSelectionLock(!!request); return () => onSelectionLock(false); }, [!!request]);
  const [browser, setBrowser] = useState<'select' | 'create'>(); const [record, setRecord] = useState<DeliveryRecord>();
  const [busy, setBusy] = useState(false); const [force, setForce] = useState(false);
  const [error, setError] = useState(''); const [unconfirmed, setUnconfirmed] = useState(false);
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  function checked(value: DeliveryRecord, fixed: Request) {
    if (value.command_id !== fixed.command_id || value.receive_id !== fixed.receive_id || value.local_project_id !== api.projectId ||
      value.local_session_id !== sid || value.action !== 'files' || value.name !== fixed.name) throw new Error('cloud_delivery_identity_mismatch');
    return value;
  }
  function adopt(value: DeliveryRecord) { setRecord(value); setForce(false); setUnconfirmed(false); }
  useEffect(() => {
    if (!active || !request || busy || record && !['preparing', 'executing'].includes(record.phase)) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try { const value = checked(await api.deliveryStatus(sid, request!.command_id, controller.signal), request!);
        if (!controller.signal.aborted) { adopt(value); if (['preparing', 'executing'].includes(value.phase)) timer = setTimeout(() => void read(), backgroundTaskPollMs); }
      } catch (failure) { if (!controller.signal.aborted) setError(cloudError(failure)); }
    }
    void read(); return () => { controller.abort(); clearTimeout(timer); };
  }, [api, sid, active, request?.command_id, busy, record?.phase]);
  function reset() {
    try { sessionStorage.removeItem(key); onSelectionLock(false); setRequest(undefined); setRecord(undefined); setForce(false); setUnconfirmed(false); setError(''); }
    catch { console.warn('cloud_files_intent_clear_failed'); setError('浏览器未能清除原保存意图，请保留原操作核对。'); }
  }
  async function prepare(choice?: FolderChoice) {
    if (busy || !active) return; setBusy(true); setError(''); setBrowser(undefined);
    try {
      const received = request ? undefined : await prepareMaterials();
      if (!alive.current) return;
      const fixed = request ?? { command_id: crypto.randomUUID(), receive_id: received!.receive_id, action: 'files' as const,
        destination_handle: api.projectId, destination_path: choice!.path, expected_directory: choice!.identity, name: choice!.folderName ?? '' };
      sessionStorage.setItem(key, JSON.stringify(fixed)); onSelectionLock(true); setRequest(fixed);
      const value = checked(await api.delivery(sid, fixed), fixed); if (alive.current) adopt(value);
    } catch (failure) { if (alive.current) {
      if (!request && failure instanceof ApiError && ['invalid_request', 'cloud_delivery_name_invalid', 'cloud_delivery_destination_forbidden', 'cloud_delivery_root_changed', 'cloud_delivery_receive_unavailable'].includes(failure.code)) reset();
      setError(cloudError(failure));
    } } finally { if (alive.current) setBusy(false); }
  }
  async function update(kind: 'query' | 'recheck' | 'execute') {
    if (!request || busy || !active) return; setBusy(true); setError('');
    if (kind === 'execute') setUnconfirmed(true);
    try {
      const value = kind === 'execute' ? await api.executeDelivery(sid, request.command_id, record?.phase === 'prepared' ? force : record?.force ?? force) :
        kind === 'recheck' ? await api.recheckDelivery(sid, request.command_id) : await api.deliveryStatus(sid, request.command_id);
      if (alive.current) adopt(checked(value, request));
    } catch (failure) { if (alive.current) setError(cloudError(failure)); } finally { if (alive.current) setBusy(false); }
  }
  const pending = busy || !active || ['preparing', 'executing'].includes(record?.phase ?? '');
  return <section className={deliveryStyles.delivery} aria-label="保存报告">
    {!request && <div className={styles.actions}><button disabled={pending || !selected.length} onClick={() => setBrowser('select')}>保存勾选文件到本地</button></div>}
    {browser && <div className={deliveryStyles.browser}><div className={styles.actions}><button onClick={() => setBrowser('select')} aria-pressed={browser === 'select'}>使用已有文件夹</button><button onClick={() => setBrowser('create')} aria-pressed={browser === 'create'}>新建文件夹</button></div>
      <DirectoryBrowser api={api} purpose="save" mode={browser} choose={choice => void prepare(choice)} close={() => setBrowser(undefined)} /></div>}
    {request && <><p className={styles.note}>保存到：{record?.destination_path ?? [request.destination_path, request.name].filter(Boolean).join('/')}</p>
      <div className={styles.actions}>{!record && <button disabled={pending} onClick={() => void prepare()}>核对原保存请求</button>}
        <button disabled={pending} onClick={() => void update('query')}>查询保存状态</button>
        {record && ['prepared', 'failed', 'partial', 'unknown'].includes(record.phase) && <button disabled={pending || unconfirmed} onClick={() => void update('recheck')}>重新预检保存</button>}
        {record && ['prepared', 'failed', 'completed'].includes(record.phase) && <button disabled={pending || unconfirmed} onClick={reset}>更换保存位置或选择</button>}</div></>}
    {record && <><ul className={deliveryStyles.paths}>{record.changes.map(change => <li key={change.path}>{change.path}</li>)}</ul><p role="status" className={styles.note}>{({ preparing: '正在预检保存位置', prepared: '保存预检已完成', executing: '正在保存文件', completed: '文件已保存到所选文件夹', partial: '保存尚未完成，请核对原进度', failed: '保存未完成', unknown: '保存状态待核对' })[record.phase]}{record.done?.length ? ` · 已保存 ${record.done.length} 个文件` : ''}</p>
      {!!record.conflicts.length && <div role="alert" className={styles.error}>同名文件内容不同：<ul>{record.conflicts.map(path => <li key={path}>{path}</li>)}</ul></div>}
      {record.force_allowed && record.phase === 'prepared' && <label className={styles.check}><input type="checkbox" checked={force} onChange={e => setForce(e.target.checked)} />确认覆盖以上同名文件</label>}
      {['prepared', 'partial', 'unknown'].includes(record.phase) && !unconfirmed && <div className={styles.actions}>
        <button disabled={pending || record.phase === 'prepared' && !!record.conflicts.length && !force} onClick={() => void update('execute')}>确认保存文件</button></div>}
      {record.error_code && <p className={styles.error}>{cloudError({ code: record.error_code })}</p>}
      {record.error_code === 'cloud_material_unavailable' && <button disabled={pending} onClick={() => { setBusy(true); void prepareMaterials(true, request?.receive_id).then(value => { if (value.receive_id !== request?.receive_id) throw new Error('cloud_delivery_identity_mismatch'); setError('材料已重新核对，请继续原保存。'); }).catch(e => setError(cloudError(e))).finally(() => { if (alive.current) setBusy(false); }); }}>重新核对报告材料</button>}</>}
    {unconfirmed && <p role="status" className={styles.note}>原保存结果未确认，请查询保存状态后继续。</p>}
    {error && <p role="alert" className={styles.error}>{error}</p>}
  </section>;
}
