import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding, CloudProfile } from '../cloudTypes';
import { cloudError, taskLabel } from '../cloudPresentation';
import { backgroundTaskPollMs } from '../uiSettings';
import CloudTaskCreate from './CloudTaskCreate';
import CloudTaskDetail from './CloudTaskDetail';
import styles from './CloudTasks.module.css';
import type { AnalysisDraft } from '../cloudReceiveTypes';

export default function CloudTasks({ api, sessionId, active, onAnalysisDraft }: { api: CloudApi; sessionId?: string; active: boolean; onAnalysisDraft?: (value: AnalysisDraft) => boolean | void }) {
  const [rows, setRows] = useState<CloudBinding[]>([]); const [profiles, setProfiles] = useState<CloudProfile[]>([]);
  const [selected, select] = useState<string>(); const [creating, create] = useState(false);
  const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const [revision, refresh] = useState(0); const [pages, setPages] = useState(1); const [hasMore, setMore] = useState(false);
  const [visible, setVisible] = useState(document.visibilityState !== 'hidden');
  const [stale, setStale] = useState<string[]>([]);
  const focusTarget = useRef<string | undefined>(undefined); const rowButtons = useRef(new Map<string, HTMLButtonElement>());
  const newButton = useRef<HTMLButtonElement>(null);
  const valid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(api.projectId) && !!sessionId;
  useEffect(() => {
    const changed = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', changed); return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  useEffect(() => { setRows([]); select(undefined); create(false); setPages(1); }, [api, sessionId]);
  useEffect(() => {
    if (!active || !visible || !valid) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function read() {
      setLoading(true);
      try {
        const available = await api.profiles(controller.signal); if (controller.signal.aborted) return;
        setProfiles(available); const values: CloudBinding[] = []; let last = 0;
        for (let i = 0; i < pages; i++) {
          const page = await api.list(sessionId!, i * 50, controller.signal); values.push(...page); last = page.length;
          if (last < 50) break;
        }
        const outdated: string[] = [];
        const current = await Promise.all(values.map(async row => {
          try {
            const value = await api.binding(sessionId!, row.binding_id, controller.signal);
            if (value.binding_id !== row.binding_id || value.local_session_id !== sessionId) throw new Error('cloud_task_identity_mismatch');
            return value;
          } catch (failure) {
            if (!controller.signal.aborted) console.warn('cloud_task_metadata_unavailable');
            outdated.push(row.binding_id);
            return row;
          }
        }));
        if (!controller.signal.aborted) {
          setRows([...new Map(current.filter(r => r.retained).map(r => [r.binding_id, r])).values()]);
          setMore(last === 50); setError('');
          setStale(outdated);
        }
      } catch (failure) { if (!controller.signal.aborted) { console.warn('cloud_tasks_read_failed'); setStale(rows.map(row => row.binding_id)); setError(cloudError(failure)); } }
      finally { if (!controller.signal.aborted) { setLoading(false); timer = setTimeout(read, backgroundTaskPollMs); } }
    }
    void read(); return () => { controller.abort(); clearTimeout(timer); };
  }, [api, sessionId, active, visible, valid, pages, revision]);
  const binding = rows.find(row => row.binding_id === selected);
  const update = () => refresh(v => v + 1);
  const back = () => { focusTarget.current = selected ?? 'new'; select(undefined); create(false); update(); };
  useEffect(() => {
    if (selected || creating || !focusTarget.current) return;
    (rowButtons.current.get(focusTarget.current) ?? newButton.current)?.focus(); focusTarget.current = undefined;
  }, [selected, creating]);
  if (!valid) return <section className={styles.panel}><p className={styles.note}>请先在已授权的本地项目中选择一个会话，再使用云端任务。</p></section>;
  if (creating) return <CloudTaskCreate api={api} sessionId={sessionId!} profiles={profiles} back={back} created={value => {
    setRows(old => [value, ...old.filter(row => row.binding_id !== value.binding_id)]); select(value.binding_id); create(false); update();
  }} />;
  if (binding?.task_id && binding.cloud) return <CloudTaskDetail key={binding.binding_id} api={api} sid={sessionId!}
    binding={binding} metadataFresh={!stale.includes(binding.binding_id) && !error} active={active && visible} back={back} changed={update} onAnalysisDraft={onAnalysisDraft} />;
  return <section className={styles.panel} aria-label="云端任务列表">
    <div className={styles.listHeader}><button ref={newButton} className={styles.primary} onClick={() => { select(undefined); create(true); }}>新增任务</button>
      <button onClick={update}>刷新</button></div>
    <p className={styles.note}>每个任务使用独立沙箱。打开和切换只查看，不创建环境。</p>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {loading && !rows.length && <p role="status" className={styles.note}>正在读取任务…</p>}
    {!loading && !rows.length && !error && <p className={styles.note}>还没有云端任务</p>}
    <div className={styles.list}>{rows.map(row => <button key={row.binding_id} ref={el => { if (el) rowButtons.current.set(row.binding_id, el); else rowButtons.current.delete(row.binding_id); }} className={styles.row} aria-label={`任务 ${row.task_id?.slice(0, 8) ?? '准备中'} · ${taskLabel(row)}`}
      onClick={() => select(row.binding_id)}><strong>任务 {row.task_id?.slice(0, 8) ?? '准备中'}</strong>
      <span>{stale.includes(row.binding_id) ? '任务状态尚未确认' : taskLabel(row)}</span><small>{row.profile_id} · 实例 {row.instance_id.slice(0, 8)}</small></button>)}</div>
    {binding && !binding.cloud && <div className={styles.notice} role="status">任务仍在准备，尚未调用模型。
      <button onClick={() => void api.retry(sessionId!, binding.create_command_id).then(update).catch(e => setError(cloudError(e)))}>核对原准备操作</button></div>}
    {hasMore && <button disabled={loading} onClick={() => setPages(v => v + 1)}>加载更多任务</button>}
  </section>;
}
