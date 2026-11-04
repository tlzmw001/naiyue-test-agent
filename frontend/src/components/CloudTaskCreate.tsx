import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding, CloudProfile } from '../cloudTypes';
import { cloudError } from '../cloudPresentation';
import styles from './CloudTasks.module.css';
import CloudSourcePicker, { type SourceScope } from './CloudSourcePicker';

export default function CloudTaskCreate({ api, sessionId, profiles, created, back }: {
  api: CloudApi; sessionId: string; profiles: CloudProfile[]; created: (value: CloudBinding) => void; back: () => void;
}) {
  const [profileId, setProfile] = useState(profiles[0]?.profile_id ?? '');
  const profile = profiles.find(p => p.profile_id === profileId);
  const [template, setTemplate] = useState(profile?.default_template ?? '');
  const [scope, setScope] = useState<SourceScope | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const frozen = useRef<object | undefined>(undefined); const source = useRef<{ source_handle: string; size: number } | undefined>(undefined);
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
      if (!source.current) {
        if (!scope) return;
        source.current = await api.source(sessionId, scope);
      }
      if (!frozen.current) frozen.current = { command_id: crypto.randomUUID(), local_session_id: sessionId,
        profile_id: profileId, template_id: template, source_handle: source.current.source_handle };
      const result = await api.create(frozen.current);
      if (result.binding.local_session_id !== sessionId || result.binding.profile_id !== profileId) throw new Error('cloud_task_identity_mismatch');
      if (alive.current) created(result.binding);
    } catch (failure) { if (alive.current) { console.warn('cloud_task_create_failed'); setError(cloudError(failure)); } }
    finally { pending.current = false; if (alive.current) setBusy(false); }
  }
  return <section className={styles.form} aria-label="新增云端任务">
    <button className={styles.link} onClick={back}>返回任务列表</button>
    <h3>新增任务</h3>
    {!profiles.length && <p role="status">尚未配置云端连接，请先配置连接并重新读取。</p>}
    <label>云端连接<select value={profileId} disabled={busy || !!frozen.current} onChange={e => {
      setProfile(e.target.value); setTemplate(profiles.find(p => p.profile_id === e.target.value)?.default_template ?? '');
    }}>{profiles.map(p => <option key={p.profile_id} value={p.profile_id}>{p.profile_id}</option>)}</select></label>
    <label>环境模板<select value={template} disabled={busy || !!frozen.current} onChange={e => setTemplate(e.target.value)}>
      {(profile?.template_ids ?? []).map(id => <option key={id} value={id}>{id}</option>)}
    </select></label>
    <CloudSourcePicker api={api} sid={sessionId} disabled={busy || !!source.current} onChange={setScope} />
    {error && <p role="alert" className={styles.error}>{error}{frozen.current ? '；再次点击会复用原准备操作。' : ''}</p>}
    <button className={styles.primary} disabled={busy || !profile || !template || (!scope && !source.current)} onClick={() => void prepare()}>
      {busy ? '正在导入并准备…' : frozen.current ? '重试原准备操作' : '导入并准备任务'}</button>
  </section>;
}
