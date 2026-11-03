import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { DeliveryAction, DeliveryRecord, DeliveryRequest } from '../cloudDeliveryTypes';
import { ApiError } from '../api';
import { cloudError } from '../cloudPresentation';
import { backgroundTaskPollMs } from '../uiSettings';
import styles from './CloudTasks.module.css';
import deliveryStyles from './CloudCodeDelivery.module.css';

type Request = Omit<DeliveryRequest, 'local_session_id'>;
const phases: Record<DeliveryRecord['phase'], string> = { preparing: '正在验证代码交付', prepared: '预检已完成，等待确认',
  executing: '正在执行代码交付', completed: '代码交付已完成', partial: '代码交付部分完成', failed: '代码交付失败', unknown: '代码交付状态待核对' };
const actions: Record<DeliveryAction, string> = { export: '导出到项目内新目录', branch: '创建本地 Git 分支', apply: '应用到当前项目' };

export default function CloudCodeDelivery({ api, sid, receiveId, resultKey, active }: {
  api: CloudApi; sid: string; receiveId: string; resultKey: string; active: boolean;
}) {
  const key = `naiyue.cloud-delivery.${api.projectId}.${sid}.${resultKey}`;
  const [intent, setIntent] = useState<Request | undefined>(() => {
    try { const value = JSON.parse(sessionStorage.getItem(key) ?? 'null');
      if (value && typeof value.command_id === 'string' && typeof value.receive_id === 'string' && value.destination_handle === api.projectId &&
        ['export', 'branch', 'apply'].includes(value.action) && typeof value.name === 'string') return value;
    } catch { console.warn('cloud_delivery_draft_read_failed'); }
  });
  const [action, setAction] = useState<DeliveryAction>(intent?.action ?? 'export');
  const [name, setName] = useState(intent?.name ?? ''); const [record, setRecord] = useState<DeliveryRecord>();
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
  function adopt(value: DeliveryRecord) { setConfirmed(false); setShown(50); setRecord(value); }
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
    const request = intent ?? { command_id: crypto.randomUUID(), receive_id: receiveId, action, destination_handle: api.projectId, name: action === 'apply' ? '' : name.trim() };
    setIntent(request); setBusy(true); setConfirmed(false); setError('');
    try {
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
    if (!intent || !record || busy || !active || !confirmed || unconfirmed || record.conflicts.length || !['prepared', 'partial', 'unknown'].includes(record.phase)) return;
    setBusy(true); setConfirmed(false); setUnconfirmed(true); setError('');
    try { const value = checked(await api.executeDelivery(sid, intent.command_id), intent); if (live()) { adopt(value); setUnconfirmed(false); } }
    catch (failure) { if (live()) setError(cloudError(failure)); } finally { if (live()) setBusy(false); }
  }
  function reset() {
    try { sessionStorage.removeItem(key); setIntent(undefined); setRecord(undefined); setConfirmed(false); setUnconfirmed(false); setError(''); }
    catch { setError('浏览器未能清除原交付草稿，请保留原操作核对。'); }
  }
  const running = record?.phase === 'preparing' || record?.phase === 'executing';
  const canExecute = record && !record.conflicts.length && ['prepared', 'partial', 'unknown'].includes(record.phase) && !unconfirmed;
  return <section className={deliveryStyles.delivery} aria-label="受控代码交付">
    <strong>代码交付</strong><p className={styles.note}>先验证固定输入和补丁，再确认写入。目标范围为当前已授权项目；接收或分析材料不会自动应用代码。</p>
    <label className={deliveryStyles.field}>代码交付方式<select aria-label="代码交付方式" value={action} disabled={busy || !!intent || !active} onChange={event => { setAction(event.target.value as DeliveryAction); setName(''); }}>
      {Object.entries(actions).map(([value, title]) => <option key={value} value={value}>{title}</option>)}</select></label>
    {action !== 'apply' && <label className={deliveryStyles.field}>{action === 'export' ? '新目录名' : 'Git 分支名'}<input aria-label={action === 'export' ? '新目录名' : 'Git 分支名'} value={name} maxLength={128}
      disabled={busy || !!intent || !active} onChange={event => setName(event.target.value)} /></label>}
    <p className={styles.note}>{action === 'export' ? '只填写当前项目内的一个新目录名，目标已存在时不会覆盖。' : action === 'branch' ?
      '只创建新分支，不切换 HEAD，不更改原工作区和索引。' : '只处理本次输入到输出的变化路径，当前内容不再匹配基线时会拒绝。'}</p>
    {action === 'apply' && <p className={styles.note}>执行前请暂停本机 Agent 和编辑器对相同文件的写入；最终核对与替换之间无法阻止不合作进程的并发改动，交付不提供全局文件事务。</p>}
    <div className={styles.actions}>{(!record || record.phase === 'unknown') && <button disabled={busy || !active || !intent && action !== 'apply' && !name.trim()} onClick={() => void prepare()}>
      {intent ? '核对原预检' : '预检代码交付'}</button>}
      {intent && <button disabled={busy || !active} onClick={() => void query()}>查询原交付状态</button>}
      {record && ['prepared', 'completed', 'failed'].includes(record.phase) && !busy && !unconfirmed && <button disabled={!active} onClick={reset}>更换或重新预检</button>}</div>
    {intent && <small className={styles.note}>原操作 {intent.command_id} · 接收 {intent.receive_id}</small>}
    {record && <><p role="status" className={styles.note}>{phases[record.phase]}</p>
      <strong>预检变化 · {record.changes.length}</strong><ul className={deliveryStyles.paths}>{record.changes.slice(0, shown).map(item => <li key={item.path}>
        {({ add: '新增', modify: '修改', delete: '删除' })[item.operation]} · {item.path}</li>)}</ul>
      {record.changes.length > shown && <div className={styles.actions}><button onClick={() => setShown(value => value + 50)}>显示更多变化</button></div>}
      {!!record.conflicts.length && <div role="alert" className={styles.error}>存在冲突，请处理后重新预检；当前操作不会执行。<ul>{record.conflicts.map(value => <li key={value}>{value}</li>)}</ul></div>}
      {record.phase === 'partial' && <p className={styles.note}>已有部分文件写入。继续会重新核对原日志和当前文件，不自动回滚用户改动。</p>}
      {(record.phase === 'unknown' || unconfirmed) && <p className={styles.note}>原交付状态尚未确认，请查询同一操作，核对固定材料和日志后再显式继续。</p>}
      {record.branch_head && <p className={styles.note}>已记录分支提交 {record.branch_head}</p>}
      {record.error_code && <p className={styles.error}>{cloudError({ code: record.error_code })}</p>}
      {canExecute && <><label className={styles.check}><input type="checkbox" checked={confirmed} disabled={busy || !active} onChange={event => setConfirmed(event.target.checked)} />我已核对目标和变化，确认执行此交付</label>
        <div className={styles.actions}><button disabled={busy || !active || !confirmed || running} onClick={() => void execute()}>{record.phase === 'prepared' ? '确认执行交付' : '确认继续原交付'}</button></div></>}
    </>}{error && <p role="alert" className={styles.error}>{error}</p>}
  </section>;
}
