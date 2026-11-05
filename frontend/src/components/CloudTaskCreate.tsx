import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding, CloudProfile } from '../cloudTypes';
import { cloudError } from '../cloudPresentation';
import styles from './CloudTasks.module.css';

export default function CloudTaskCreate({ api, sessionId, profiles, created, back }: {
  api: CloudApi; sessionId: string; profiles: CloudProfile[]; created: (value: CloudBinding) => void; back: () => void;
}) {
  const [profileId, setProfile] = useState(profiles[0]?.profile_id ?? '');
  const profile = profiles.find(p => p.profile_id === profileId);
  const [template, setTemplate] = useState(profile?.default_template ?? '');
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const frozen = useRef<object | undefined>(undefined);
  const alive = useRef(true); const pending = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!frozen.current && !pending.current && !profile && profiles.length) {
      setProfile(profiles[0].profile_id); setTemplate(profiles[0].default_template);
    }
  }, [profiles, profile]);
  async function prepare() {
    if (pending.current || !profile || !template) return;
    pending.current = true; setBusy(true); setError('');
    try {
      if (!frozen.current) frozen.current = { command_id: crypto.randomUUID(), local_session_id: sessionId,
        profile_id: profileId, template_id: template };
      const result = await api.create(frozen.current);
      if (result.binding.local_session_id !== sessionId || result.binding.profile_id !== profileId) throw new Error('cloud_task_identity_mismatch');
      if (alive.current) created(result.binding);
    } catch (failure) { if (alive.current) { console.warn('cloud_task_create_failed'); setError(cloudError(failure)); } }
    finally { pending.current = false; if (alive.current) setBusy(false); }
  }
  const connectionId = profile?.connection_id ?? profile?.profile_id ?? '';
  const connections = [...new Set(profiles.map(p => p.connection_id ?? p.profile_id))];
  const networks = profiles.filter(p => (p.connection_id ?? p.profile_id) === connectionId);
  function choose(id: string) { setProfile(id); setTemplate(profiles.find(p => p.profile_id === id)?.default_template ?? ''); }
  return <section className={styles.form} aria-label="新增云端任务">
    <button className={styles.link} onClick={back}>返回任务列表</button>
    <h3>新增任务</h3>
    {!profiles.length && <p role="status">尚未配置云端连接，请先配置连接并重新读取。</p>}
    <label>云端连接<select value={connectionId} disabled={busy || !!frozen.current} onChange={e => {
      const next = profiles.find(p => (p.connection_id ?? p.profile_id) === e.target.value); if (next) choose(next.profile_id);
    }}>{connections.map(id => <option key={id} value={id}>{id}</option>)}</select></label>
    {networks.some(p => p.network_id) && <label>网络入口<select value={profileId} disabled={busy || !!frozen.current} onChange={e => choose(e.target.value)}>
      {networks.map(p => <option key={p.profile_id} value={p.profile_id}>{p.network_id || '不使用本机转发'} · {p.profile_id}</option>)}
    </select></label>}
    <label>环境模板<select value={template} disabled={busy || !!frozen.current} onChange={e => setTemplate(e.target.value)}>
      {(profile?.template_ids ?? []).map(id => <option key={id} value={id}>{id}</option>)}
    </select></label>
    <p className={styles.note}>先准备环境，进入对话后再添加文件或直接发送任务。</p>
    {error && <p role="alert" className={styles.error}>{error}{frozen.current ? '；再次点击会复用原准备操作。' : ''}</p>}
    <button className={styles.primary} disabled={busy || !profile || !template} onClick={() => void prepare()}>
      {busy ? '正在准备环境…' : frozen.current ? '重试原准备操作' : '创建并准备任务'}</button>
  </section>;
}
