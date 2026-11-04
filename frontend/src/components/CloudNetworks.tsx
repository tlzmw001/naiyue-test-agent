import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudProfile } from '../cloudTypes';
import { cloudError } from '../cloudPresentation';
import styles from './CloudTasks.module.css';
export interface ServiceTarget { address: string; enabled: boolean }
export interface NetworkView {
  endpoints: { bind_host: string; http_port: number; grpc_port: number };
  state: { revision: number; slots: Partial<Record<'http' | 'grpc', ServiceTarget>>; switching: unknown };
  local: { pending?: { slot: 'http' | 'grpc'; target: ServiceTarget; expected_revision: number } | null };
  status: Record<string, string>;
}
export default function CloudNetworks({ api, profiles, close }: { api: CloudApi; profiles: CloudProfile[]; close: () => void }) {
  const available = profiles.filter(p => p.network_id); const [profile, setProfile] = useState(available[0]?.profile_id ?? '');
  const [value, setValue] = useState<NetworkView>(); const [http, setHttp] = useState(''); const [grpc, setGrpc] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [message, setMessage] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    if (!profile) return;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>; let first = true;
    setValue(undefined); setHttp(''); setGrpc(''); setError('');
    async function read() {
      try { const result = await api.network(profile, abort.signal); if (abort.signal.aborted) return;
        setValue(result); if (first) { setHttp(result.state.slots.http?.address ?? ''); setGrpc(result.state.slots.grpc?.address ?? ''); first = false; }
      } catch (e) { if (!abort.signal.aborted) { console.warn('cloud_network_read_failed'); setError(cloudError(e)); } }
      finally { if (!abort.signal.aborted) timer = setTimeout(read, 5000); }
    }
    void read(); return () => { abort.abort(); clearTimeout(timer); };
  }, [api, profile]);
  async function apply(slot: 'http' | 'grpc', target: ServiceTarget, revision: number) {
    setBusy(true); setError(''); setMessage('');
    try { setValue(await api.changeNetwork(profile, slot, target, revision)); setMessage('配置已应用，后续任务连接使用当前目标。'); }
    catch (e) { console.warn('cloud_network_apply_failed'); setError(cloudError(e)); }
    finally { setBusy(false); }
  }
  async function test(address: string) {
    setBusy(true); setError(''); setMessage('');
    try { await api.testNetwork(profile, { address, enabled: true }); setMessage('Windows 到目标端口的 TCP 连接成功；尚未验证 HTTP、gRPC 或证书。'); }
    catch (e) { console.warn('cloud_network_test_failed'); setError(cloudError(e)); }
    finally { setBusy(false); }
  }
  const pending = value?.local.pending;
  return <dialog ref={dialog} className={`${styles.dialog} ${styles.networkDialog}`} aria-labelledby="cloud-network-title" onCancel={e => { e.preventDefault(); close(); }}>
    <h3 id="cloud-network-title">服务连接</h3>
    <p>通过本机 Windows 访问待测服务。关闭此窗口后，连接仍由本机服务保持。</p>
    {!available.length ? <p>尚未分配服务入口，请管理员在云端登记两个端口，并在本机连接配置中填写 network_id 和 SSH 别名。</p> : <>
      <label>云端连接<select disabled={busy} value={profile} onChange={e => setProfile(e.target.value)}>{available.map(p => <option key={p.profile_id} value={p.profile_id}>{p.profile_id}</option>)}</select></label>
      <p>修改一个入口会影响使用它的所有任务。请先停止运行中的测试和后台服务；切换可能中断已有连接。</p>
      {(['http', 'grpc'] as const).map(slot => {
        const address = slot === 'http' ? http : grpc; const title = slot === 'http' ? 'HTTP' : 'gRPC'; const status = value?.status[slot];
        return <fieldset key={slot} disabled={busy || !value || !!pending} className={styles.sourcePicker}>
          <legend>{title}</legend>
          {value && <p>容器入口：{value.endpoints.bind_host}:{value.endpoints[slot === 'http' ? 'http_port' : 'grpc_port']}</p>}
          <p role="status">{status === 'connected' ? '隧道已建立' : status === 'connecting' ? '正在连接' : status === 'disconnected' ? '未连接' : status ? cloudError({ code: status }) : '正在读取'}</p>
          <label>{slot === 'http' ? 'HTTP / HTTPS 地址' : 'gRPC 地址'}<input value={address} onChange={e => slot === 'http' ? setHttp(e.target.value) : setGrpc(e.target.value)} placeholder={slot === 'http' ? 'https://域名:端口/路径' : 'grpc://域名:端口 或 grpcs://域名:端口'} /></label>
          <p className={styles.note}>{slot === 'grpc' ? 'grpc 使用明文，grpcs 使用 TLS。' : 'https 使用 TLS，保留原始域名校验证书。'}地址中不要填写密码、令牌或查询参数。</p>
          <div className={styles.actions}><button disabled={!address} onClick={() => void test(address)}>测试 {title} 端口</button>
            <button disabled={!address} onClick={() => void apply(slot, { address, enabled: true }, value!.state.revision)}>应用 {title} 配置</button>
            <button disabled={!value?.state.slots[slot]?.enabled} onClick={() => void apply(slot, { ...value!.state.slots[slot]!, enabled: false }, value!.state.revision)}>断开 {title}</button></div>
        </fieldset>;
      })}
      {pending && <div role="status"><p>上次切换尚未确认，正在重试原操作。可取消切换并恢复原目标。</p>
        <button disabled={busy} onClick={() => void apply(pending.slot, pending.target, pending.expected_revision)}>重试原切换</button>
        <button disabled={busy} onClick={async () => { setBusy(true); setError(''); try { setValue(await api.cancelNetwork(profile)); } catch (e) { console.warn('cloud_network_cancel_failed'); setError(cloudError(e)); } finally { setBusy(false); } }}>取消切换</button></div>}
    </>}
    {message && <p role="status">{message}</p>}{error && <p role="alert" className={styles.error}>{error}</p>}
    <div className={styles.actions}><button onClick={close}>关闭</button></div>
  </dialog>;
}
