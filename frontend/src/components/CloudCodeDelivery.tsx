import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { ReceiveRecord } from '../cloudReceiveTypes';
import type { DeliveryAction, DeliveryRecord, DeliveryRequest } from '../cloudDeliveryTypes';
import { ApiError } from '../api';
import { cloudError } from '../cloudPresentation';
import { backgroundTaskPollMs } from '../uiSettings';
import styles from './CloudTasks.module.css';
import deliveryStyles from './CloudCodeDelivery.module.css';

type Request = Omit<DeliveryRequest, 'local_session_id'>;
const phases: Record<DeliveryRecord['phase'], string> = { preparing: '正在验证代码交付', prepared: '预检已完成，等待确认',
  executing: '正在执行代码交付', completed: '代码交付已完成', partial: '代码交付部分完成', failed: '代码交付失败', unknown: '代码交付状态待核对' };
const actions = { apply: '应用到当前代码', branch: '创建新分支放入代码' };

export default function CloudCodeDelivery({ api, sid, receiveId, resultKey, active, branchAvailable = false, prepareMaterials }: {
  api: CloudApi; sid: string; receiveId?: string; resultKey: string; active: boolean; branchAvailable?: boolean; prepareMaterials?: (refresh?: boolean, fixedId?: string) => Promise<ReceiveRecord>;
}) {
  const key = `naiyue.cloud-delivery.${api.projectId}.${sid}.${resultKey}`;
  const [intent, setIntent] = useState<Request | undefined>(() => {
    try { const value = JSON.parse(sessionStorage.getItem(key) ?? 'null');
      if (value && typeof value.command_id === 'string' && typeof value.receive_id === 'string' && value.destination_handle === api.projectId &&
        ['export', 'branch', 'apply'].includes(value.action) && typeof value.name === 'string') return value;
    } catch { console.warn('cloud_delivery_draft_read_failed'); }
  });
  const [action, setAction] = useState<DeliveryAction>(intent?.action ?? 'apply');
  const [name, setName] = useState(intent?.name ?? ''); const [record, setRecord] = useState<DeliveryRecord>();
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false); const [confirmed, setConfirmed] = useState(false);
  const [unconfirmed, setUnconfirmed] = useState(false); const [error, setError] = useState(''); const [shown, setShown] = useState(50);
  const context = `${api.projectId}:${sid}:${resultKey}:${receiveId}`;
  const current = useRef({ context, alive: true, active }); current.current.context = context; current.current.active = active;
  useEffect(() => { current.current.alive = true; return () => { current.current.alive = false; }; }, []);
  const live = () => current.current.alive && current.current.context === context;
  function checked(value: DeliveryRecord, request: Request) {
    if (value.command_id !== request.command_id || value.local_project_id !== api.projectId || value.local_session_id !== sid ||
      value.receive_id !== request.receive_id || value.action !== request.action || value.name !== request.name) throw new Error('cloud_delivery_identity_mismatch');
    return value;
  }
  function adopt(value: DeliveryRecord) { setConfirmed(false); setForce(false); setShown(50); setRecord(value); }
  useEffect(() => {
    if (!active || !intent || busy || record && !['preparing', 'executing'].includes(record.phase)) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const value = checked(await api.deliveryStatus(sid, intent!.command_id, controller.signal), intent!);
        if (!controller.signal.aborted && live()) {
          adopt(value); setError(''); setUnconfirmed(false);
          if (['preparing', 'executing'].includes(value.phase)) timer = setTimeout(() => void read(), backgroundTaskPollMs);
        }
      } catch (failure) { if (!controller.signal.aborted && live()) setError(cloudError(failure)); }
    }
    void read(); return () => { controller.abort(); clearTimeout(timer); };
  }, [api, sid, context, active, intent?.command_id, busy, record?.phase]);
  async function prepare() {
    if (busy || !active) return;
    setBusy(true); setConfirmed(false); setError('');
    try {
      const id = intent?.receive_id ?? receiveId ?? (await prepareMaterials!()).receive_id;
      if (!live()) return;
      const request = intent ?? { command_id: crypto.randomUUID(), receive_id: id, action, destination_handle: api.projectId, name: action === 'apply' ? '' : name.trim() };
      setIntent(request);
      sessionStorage.setItem(key, JSON.stringify(request));
      const value = checked(await api.delivery(sid, request), request);
      if (live()) { adopt(value); setUnconfirmed(false); }
    } catch (failure) { if (live()) {
      if (!intent && failure instanceof ApiError && ['invalid_request', 'cloud_delivery_name_invalid', 'cloud_delivery_destination_forbidden', 'cloud_delivery_receive_unavailable'].includes(failure.code)) reset();
      setError(cloudError(failure));
    } }
    finally { if (live()) setBusy(false); }
  }
  async function query() {
    if (!intent || busy || !active) return; setBusy(true); setConfirmed(false); setError('');
    try { const value = checked(await api.deliveryStatus(sid, intent.command_id), intent); if (live()) { adopt(value); setUnconfirmed(false); } }
    catch (failure) { if (live()) setError(cloudError(failure)); } finally { if (live()) setBusy(false); }
  }
  async function execute() {
    if (!intent || !record || busy || !active || !confirmed || unconfirmed || record.conflicts.length && !(record.force_allowed && (record.phase === 'prepared' ? force : record.force)) || !['prepared', 'partial', 'unknown'].includes(record.phase)) return;
    setBusy(true); setConfirmed(false); setUnconfirmed(true); setError('');
    try { const value = checked(await api.executeDelivery(sid, intent.command_id, record.phase === 'prepared' ? force : record.force ?? force), intent); if (live()) { adopt(value); setUnconfirmed(false); } }
    catch (failure) { if (live()) setError(cloudError(failure)); } finally { if (live()) setBusy(false); }
  }
  function reset() {
    try { sessionStorage.removeItem(key); setIntent(undefined); setRecord(undefined); setConfirmed(false); setUnconfirmed(false); setError(''); }
    catch { setError('浏览器未能清除原交付草稿，请保留原操作核对。'); }
  }
  const running = record?.phase === 'preparing' || record?.phase === 'executing';
  const canExecute = record && (!record.conflicts.length || record.force_allowed) && ['prepared', 'partial', 'unknown'].includes(record.phase) && !unconfirmed;
  return <section className={deliveryStyles.delivery} aria-label="受控代码交付">
    <strong>代码取回</strong>
    <label className={deliveryStyles.field}>代码交付方式<select aria-label="代码交付方式" value={action} disabled={busy || !!intent || !active} onChange={event => { setAction(event.target.value as DeliveryAction); setName(''); }}>
      {intent?.action === 'export' && <option value="export">原目录导出操作（旧版本）</option>}
      {Object.entries(actions).filter(([value]) => value !== 'branch' || branchAvailable || intent?.action === 'branch').map(([value, title]) => <option key={value} value={value}>{title}</option>)}</select></label>
    {action !== 'apply' && <label className={deliveryStyles.field}>{action === 'export' ? '新目录名' : 'Git 分支名'}<input aria-label={action === 'export' ? '新目录名' : 'Git 分支名'} value={name} maxLength={128}
      disabled={busy || !!intent || !active} onChange={event => setName(event.target.value)} /></label>}
    <div className={styles.actions}>{(!record || record.phase === 'unknown') && <button disabled={busy || !active || !intent && action !== 'apply' && !name.trim()} onClick={() => void prepare()}>
      {intent ? '核对原预检' : '预检代码交付'}</button>}
      {intent && <button disabled={busy || !active} onClick={() => void query()}>查询原交付状态</button>}
      {record && ['prepared', 'completed', 'failed', 'partial'].includes(record.phase) && !busy && !unconfirmed && <button disabled={!active} onClick={() => { if (record.phase === 'partial') { setBusy(true); void api.recheckDelivery(sid, intent!.command_id).then(value => { if (live()) adopt(checked(value, intent!)); }).catch(e => setError(cloudError(e))).finally(() => { if (live()) setBusy(false); }); } else reset(); }}>更换或重新预检</button>}</div>
    {intent && <details><summary>操作详情</summary><small className={styles.note}>原操作 {intent.command_id} · 接收 {intent.receive_id}</small></details>}
    {record && <><p role="status" className={styles.note}>{phases[record.phase]}</p>
      <strong>预检变化 · {record.changes.length}</strong><ul className={deliveryStyles.paths}>{record.changes.slice(0, shown).map(item => <li key={item.path}>
        {({ add: '新增', modify: '修改', delete: '删除' })[item.operation]} · {item.path}</li>)}</ul>
      {record.changes.length > shown && <div className={styles.actions}><button onClick={() => setShown(value => value + 50)}>显示更多变化</button></div>}
      {!!record.conflicts.length && <div role="alert" className={styles.error}>存在本地内容差异，请处理后重新预检，或明确确认覆盖以下路径。<ul>{record.conflicts.map(value => <li key={value}>{value}</li>)}</ul></div>}
      {record.phase === 'partial' && <p className={styles.note}>交付尚未完成。继续会核对原日志和当前文件，不自动回滚用户改动。</p>}
      {(record.phase === 'unknown' || unconfirmed) && <p className={styles.note}>原交付状态尚未确认，请查询同一操作，核对固定材料和日志后再显式继续。</p>}
      {record.branch_head && <p className={styles.note}>已记录分支提交 {record.branch_head}</p>}
      {record.error_code && <p className={styles.error}>{cloudError({ code: record.error_code })}</p>}
      {record.error_code === 'cloud_material_unavailable' && prepareMaterials && <button disabled={busy || !active} onClick={() => { setBusy(true); void prepareMaterials(true, intent?.receive_id).then(value => { if (value.receive_id !== intent?.receive_id) throw new Error('cloud_delivery_identity_mismatch'); if (live()) setError('材料已重新核对，请继续原交付。'); }).catch(e => setError(cloudError(e))).finally(() => { if (live()) setBusy(false); }); }}>重新核对代码材料</button>}
      {record.force_allowed && record.phase === 'prepared' && <label className={styles.check}><input type="checkbox" checked={force} onChange={e => { setForce(e.target.checked); setConfirmed(false); }} />强制应用：覆盖或删除以上冲突路径，保留其他本地文件</label>}
      {record.base_result_id && <small className={styles.note}>基于上次完整应用的结果 {record.base_result_id.slice(0, 8)} 预检</small>}
      {canExecute && <><label className={styles.check}><input type="checkbox" checked={confirmed} disabled={busy || !active} onChange={event => setConfirmed(event.target.checked)} />我已核对目标和变化，确认执行此交付</label>
        <div className={styles.actions}><button disabled={busy || !active || !confirmed || running || !!record.conflicts.length && !force && record.phase === 'prepared'} onClick={() => void execute()}>{record.phase === 'prepared' ? '确认执行交付' : '确认继续原交付'}</button></div></>}
    </>}{error && <p role="alert" className={styles.error}>{error}</p>}
  </section>;
}
