import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudProfile } from '../cloudTypes';
import { cloudError } from '../cloudPresentation';
import { targetAddress, targetForm, type TargetForm } from '../cloudTargetForm';
import styles from './CloudTasks.module.css';
export interface ServiceTarget { address: string; enabled: boolean; name?: string }
export interface NetworkView {
  endpoints: { bind_host: string; ports: Record<string, number> };
  state: { revision: number; slots: Record<string, ServiceTarget>; switching: unknown };
  local: { pending?: { slot: string; target: ServiceTarget; expected_revision: number } | null };
  status: Record<string, string>;
}
function own<T>(values: Record<string, T> | undefined, key: string): T | undefined {
  return values && Object.hasOwn(values, key) ? values[key] : undefined;
}
export default function CloudNetworks({ api, profiles, close }: { api: CloudApi; profiles: CloudProfile[]; close: () => void }) {
  const available = profiles.filter(p => p.network_id); const [profile, setProfile] = useState(available[0]?.profile_id ?? '');
  const [value, setValue] = useState<NetworkView>(); const [drafts, setDrafts] = useState<Record<string, ServiceTarget & TargetForm>>({});
  const [readError, setReadError] = useState('');
  const [feedbackSlot, setFeedbackSlot] = useState<string>();
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [message, setMessage] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    if (!profile) return;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>; let first = true;
    setValue(undefined); setDrafts({}); setError(''); setReadError(''); setMessage(''); setFeedbackSlot(undefined);
    async function read() {
      try { const result = await api.network(profile, abort.signal); if (abort.signal.aborted) return;
        setValue(result); setReadError(''); if (first) { setDrafts(Object.fromEntries(Object.entries(result.state.slots).map(([key, target]) => [key, { ...target, ...targetForm(target.address) }]))); first = false; }
      } catch (e) { if (!abort.signal.aborted) {
        console.warn('cloud_network_read_failed'); setReadError(cloudError(e));
        setValue(previous => previous && ({ ...previous, status: Object.fromEntries(Object.entries(previous.status).map(
          ([slot, status]) => [slot, status === 'connected' || status === 'connecting' ? 'reconnecting' : status])) }));
      } }
      finally { if (!abort.signal.aborted) timer = setTimeout(read, 5000); }
    }
    void read(); return () => { abort.abort(); clearTimeout(timer); };
  }, [api, profile]);
  async function apply(slot: string, target: ServiceTarget, revision: number) {
    setFeedbackSlot(slot); setBusy(true); setError(''); setMessage('');
    try { setValue(await api.changeNetwork(profile, slot, target, revision)); setMessage('配置已应用，后续任务连接使用当前目标。'); }
    catch (e) { console.warn('cloud_network_apply_failed'); setError(cloudError(e)); }
    finally { setBusy(false); }
  }
  async function test(slot: string, target: ServiceTarget) {
    setFeedbackSlot(slot); setBusy(true); setError(''); setMessage('');
    try { await api.testNetwork(profile, target); setMessage('Windows 到目标端口的 TCP 连接成功；尚未验证业务协议或证书。'); }
    catch (e) { console.warn('cloud_network_test_failed'); setError(cloudError(e)); }
    finally { setBusy(false); }
  }
  const pending = value?.local.pending;
  function edit(slot: string, fields: Partial<ServiceTarget & TargetForm>) {
    setDrafts(previous => ({ ...previous, [slot]: { ...targetForm(''), ...(own(previous, slot) ?? { enabled: true }), ...fields, enabled: true } }));
  }
  function submit(slot: string, draft: ServiceTarget & TargetForm, testing: boolean) {
    try {
      const target = { address: targetAddress(draft), enabled: true, ...(draft.name ? { name: draft.name } : {}) };
      if (testing) void test(slot, target); else void apply(slot, target, value!.state.revision);
    } catch (failure) { setFeedbackSlot(slot); setMessage(''); setError((failure as Error).message); }
  }
  return <dialog ref={dialog} className={`${styles.resultsDialog} ${styles.networkDialog}`} aria-labelledby="cloud-network-title" onCancel={e => { e.preventDefault(); close(); }}>
    <header><h3 id="cloud-network-title">服务连接</h3><button onClick={close} aria-label="关闭">×</button></header>
    <div className={styles.networkBody}>
    <p>通过本机 Windows 访问待测服务。关闭此窗口后，连接仍由本机服务保持。</p>
    {!available.length ? <p>尚未分配服务入口，请管理员登记端口，并在本机连接配置中填写自己的 network_id 和 SSH 别名。</p> : <>
      <label>云端连接<select disabled={busy} value={profile} onChange={e => setProfile(e.target.value)}>{available.map(p => <option key={p.profile_id} value={p.profile_id}>{p.profile_id}</option>)}</select></label>
      <p>修改一个入口会影响使用它的所有任务。请先停止运行中的测试和后台服务；切换可能中断已有连接。</p>
      {readError && <p role="alert" className={styles.error}>{readError}</p>}
      {!value && <p role="status">正在读取服务入口</p>}
      {Object.entries(value?.endpoints.ports ?? {}).map(([slot, port], index) => {
        const draft = own(drafts, slot) ?? { ...targetForm(''), enabled: true }; const title = draft.name?.trim() || `连接${index + 1}`; const status = own(value?.status, slot);
        return <fieldset key={slot} disabled={busy || !value || !!pending} className={styles.sourcePicker}>
          <legend>{title}</legend>
          <p>容器入口：{value!.endpoints.bind_host}:{port}</p>
          <p role="status">{status === 'connected' ? '隧道已建立' : status === 'reconnecting' ? '连接待确认，正在恢复' : status === 'connecting' ? '正在连接' : status === 'disconnected' ? '未连接' : status ? cloudError({ code: status }) : '正在读取'}</p>
          <div className={styles.networkFields}><label>名称<input aria-label={`${title} 名称`} maxLength={80} value={draft.name ?? ''} placeholder={`连接${index + 1}`} onChange={e => edit(slot, { name: e.target.value })} /></label>
          <label>类型<select aria-label={`${title} 类型`} value={draft.type} onChange={e => edit(slot, { type: e.target.value as TargetForm['type'] })}>
            <option value="http">HTTP</option><option value="grpc">gRPC</option><option value="tcp">TCP</option></select></label>
          <label className={styles.networkTls}><input type="checkbox" aria-label={`${title} TLS`} checked={draft.tls} onChange={e => edit(slot, { tls: e.target.checked })} />使用 TLS 加密</label>
          <label>目标地址<input aria-label={`${title} 目标地址`} value={draft.address} onChange={e => edit(slot, e.target.value.includes('://') ? targetForm(e.target.value) : { address: e.target.value })} placeholder="域名 或 10.75.192.3:5000" /></label>
          </div><p className={styles.note}>可直接粘贴完整 URL，类型和 TLS 会自动填写。gRPC、TCP 请填写端口；地址中不要填写密码、令牌或查询参数。</p>
          <div className={styles.actions}><button disabled={!draft.address} onClick={() => submit(slot, draft, true)}>测试 {title} 端口</button>
            <button disabled={!draft.address} onClick={() => submit(slot, draft, false)}>应用 {title} 配置</button>
            <button disabled={!own(value?.state.slots, slot)?.enabled} onClick={() => void apply(slot, { ...own(value!.state.slots, slot)!, enabled: false }, value!.state.revision)}>断开 {title}</button></div>
          {feedbackSlot === slot && <div className={styles.networkFeedback}>
            {busy && <p role="status">正在处理连接请求…</p>}
            {message && <p role="status">{message}</p>}
            {error && <p role="alert" className={styles.error}>{error}</p>}
          </div>}
        </fieldset>;
      })}
      {pending && <div role="status"><p>上次切换尚未确认，正在重试原操作。可取消切换并恢复原目标。</p>
        <button disabled={busy} onClick={() => void apply(pending.slot, pending.target, pending.expected_revision)}>重试原切换</button>
        <button disabled={busy} onClick={async () => { setFeedbackSlot(undefined); setMessage(''); setBusy(true); setError(''); try { setValue(await api.cancelNetwork(profile)); } catch (e) { console.warn('cloud_network_cancel_failed'); setError(cloudError(e)); } finally { setBusy(false); } }}>取消切换</button></div>}
    </>}
    {error && (!feedbackSlot || !own(value?.endpoints.ports, feedbackSlot)) && <p role="alert" className={styles.error}>{error}</p>}
    </div>
  </dialog>;
}
