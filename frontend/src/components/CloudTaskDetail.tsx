import { useCallback, useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding, CloudMessage, CloudModels, CloudOperation } from '../cloudTypes';
import { canSend, cloudError, liveTools, messageRow, taskLabel } from '../cloudPresentation';
import { useCloudObservation } from '../useCloudObservation';
import { useCloudDraft } from '../useCloudDraft';
import { backgroundTaskPollMs } from '../uiSettings';
import Conversation from './Conversation';
import CloudTaskActions from './CloudTaskActions';
import ModelMenu from './ModelMenu';
import CloudResults from './CloudResults';
import type { CloudAction } from './CloudTaskActions';
import styles from './CloudTasks.module.css';

export default function CloudTaskDetail({ api, sid, binding, active, metadataFresh = true, back, changed }: {
  api: CloudApi; sid: string; binding: CloudBinding; active: boolean; metadataFresh?: boolean; back: () => void; changed: () => void;
}) {
  const [thread, setThread] = useState<string>(); const [revision, setRevision] = useState(0);
  const [models, setModels] = useState<CloudModels>(); const [modelsError, setModelsError] = useState('');
  const [modelMenu, setModelMenu] = useState(false); const closeModels = useCallback(() => setModelMenu(false), []);
  const observation = useCloudObservation(api, sid, binding.binding_id, binding.task_id!, active, thread);
  const draft = useCloudDraft(api, sid, binding.binding_id);
  const [operations, setOperations] = useState<CloudOperation[]>([]); const [error, setError] = useState('');
  const [busy, setBusy] = useState(false); const [sending, setSending] = useState(false);
  const pending = useRef(false); const alive = useRef(true);
  const message = useRef<{ id: string; version: number } | undefined>(undefined);
  const intent = useRef<{ kind: string; body: Record<string, unknown> } | undefined>(undefined);
  const [unconfirmed, setUnconfirmed] = useState(false);
  const acknowledgement = useRef(draft.acknowledged); acknowledgement.current = draft.acknowledged;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!active) { closeModels(); return; }
    const controller = new AbortController(); setModelsError('');
    void api.models(sid, binding.binding_id, controller.signal).then(value => { if (!controller.signal.aborted) setModels(value); })
      .catch(failure => { if (!controller.signal.aborted) { console.warn('cloud_models_read_failed'); setModels(undefined); setModelsError(cloudError(failure)); } });
    return () => controller.abort();
  }, [api, sid, binding.binding_id, binding.cloud?.generation, active, closeModels, revision]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const values = await api.operations(sid, binding.binding_id, controller.signal);
        if (controller.signal.aborted) return;
        setOperations(values);
        const sent = values.find(op => op.command_id === message.current?.id);
        if (sent && ['accepted', 'succeeded', 'failed', 'rejected'].includes(sent.phase)) {
          if (['accepted', 'succeeded'].includes(sent.phase)) void acknowledgement.current(message.current!.version);
          message.current = undefined; setSending(false); changed();
        }
        if (values.some(op => op.kind === 'delete' && op.phase === 'succeeded')) { changed(); back(); return; }
      } catch (failure) { if (!controller.signal.aborted) { console.warn('cloud_commands_read_failed'); setError(cloudError(failure)); } }
      finally { if (!controller.signal.aborted) timer = setTimeout(read, backgroundTaskPollMs); }
    }
    void read(); return () => { controller.abort(); clearTimeout(timer); };
  }, [api, sid, binding.binding_id, active, revision]);
  async function dispatch(kind: string, body: Record<string, unknown>) {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(''); intent.current = { kind, body };
    try {
      const op = kind === 'message' ? await api.message(sid, binding.binding_id, body) :
        await api.command(sid, binding.binding_id, kind as 'control' | 'environment' | 'delete', body);
      if (!alive.current) return;
      setOperations(old => [op, ...old.filter(v => v.command_id !== op.command_id)]); setUnconfirmed(false);
      setRevision(v => v + 1); changed();
    } catch (failure) { if (alive.current) {
      console.warn('cloud_command_submit_failed'); setError(cloudError(failure));
      const code = (failure as { code?: string })?.code;
      const rejected = ['cloud_binding_conflict', 'cloud_draft_conflict', 'cloud_command_capacity', 'cloud_preparation_pending'].includes(code ?? '');
      setUnconfirmed(!rejected);
      if (rejected) { intent.current = undefined; message.current = undefined; setSending(false); changed(); }
    } }
    finally { pending.current = false; if (alive.current) setBusy(false); }
  }
  const execute: CloudAction = async (kind, body) => { if (metadataFresh) await dispatch(kind, { command_id: crypto.randomUUID(), ...body }); };
  async function send() {
    if (!metadataFresh || !canSend(binding.cloud) || sending || !draft.text.trim() || !draft.model_id) return;
    setSending(true);
    try {
      const saved = await draft.save(); const id = crypto.randomUUID(); message.current = { id, version: saved.version };
      await dispatch('message', { command_id: id, expected_binding_revision: binding.revision,
        text: saved.text, model_id: saved.model_id, thinking_level: saved.thinking_level, draft_revision: saved.draft_revision });
    } catch (failure) { if (alive.current) { setError(cloudError(failure)); setSending(false); } }
  }
  async function retryIntent() {
    const fixed = intent.current; if (!fixed) return;
    try {
      const op = await api.operation(sid, String(fixed.body.command_id));
      await api.retry(sid, op.command_id); setUnconfirmed(false); setRevision(v => v + 1); changed();
    } catch (failure) {
      if ((failure as { code?: string }).code === 'cloud_operation_not_found') await dispatch(fixed.kind, fixed.body);
      else setError(cloudError(failure));
    }
  }
  const raw = new Map(observation.rows.map(row => [row.id, row]));
  return <section className={styles.detail} aria-label="云端任务详情">
    <header className={styles.detailHeader}><button className={styles.link} onClick={back}>返回任务列表</button>
      <strong>任务 {binding.task_id?.slice(0, 8)}</strong><span>{taskLabel(binding)}</span>
      <small>{binding.profile_id} · 实例 {binding.instance_id.slice(0, 8)}</small></header>
    <div className={styles.controlArea}>
      {!metadataFresh && <p role="status" className={styles.error}>任务状态尚未确认，保留上次内容；恢复连接后再操作。</p>}
      <CloudTaskActions api={api} sid={sid} binding={binding} busy={busy || !metadataFresh} execute={execute}
        forceReleaseAllowed={!operations.some(op => op.kind !== 'create' && ['reserved', 'unknown', 'waiting_runner'].includes(op.phase))} />
      {binding.cloud?.last_error_code && <p role="status" className={styles.error}>上次执行需要检查：{cloudError({ code: binding.cloud.last_error_code })}</p>}
      {operations.slice(0, 4).map(op => <p key={op.command_id} className={styles.note}>
        {op.kind === 'message' && op.phase === 'accepted' ? '云端已接管执行；业务中转仍需 Windows 在线。' :
          ({ reserved: '操作已在本机记录，等待云端确认', accepted: '云端已受理', succeeded: '操作已完成', failed: '操作失败', rejected: '操作未受理', unknown: '原操作结果需核对', waiting_runner: '等待执行端连接' } as Record<string, string>)[op.phase] ?? '正在处理原操作'}
        {op.error_code && <span> · {cloudError({ code: op.error_code })}</span>}
        {['unknown', 'waiting_runner'].includes(op.phase) && <button className={styles.link} onClick={() => {
          void api.retry(sid, op.command_id).then(() => { setRevision(v => v + 1); changed(); }).catch(e => setError(cloudError(e)));
        }}>继续核对原操作</button>}</p>)}
      {error && <p role="alert" className={styles.error}>{error}</p>}
      {unconfirmed && <button onClick={() => void retryIntent()}>核对并重试原请求</button>}
      <label className={styles.thread}>查看过程<select aria-label="查看云端根子线程" value={thread ?? ''} onChange={e => setThread(e.target.value || undefined)}>
        <option value="">主任务</option>{observation.threads.filter(t => t.role !== 'root').map(t => <option key={t.thread_id} value={t.thread_id}>子任务 {t.agent ?? t.thread_id.slice(0, 8)}</option>)}
      </select></label>
      {observation.hasMoreThreads && <button disabled={observation.threadsLoading} onClick={() => void observation.loadThreads()}>加载更多子线程</button>}
      {observation.error && <p role="status" className={styles.error}>{observation.error}<button className={styles.link} onClick={observation.reconnect}>重新连接</button></p>}
      {observation.snapshot?.live.capture_incomplete && <p role="status" className={styles.note}>实况存在缺口，以已保存档案为准。</p>}
    </div>
    <CloudResults api={api} sid={sid} binding={binding} active={active} changed={changed} />
    <div className={styles.process}>
      {observation.loading && <p className={styles.note}>正在读取云端过程…</p>}
      {!observation.loading && !observation.rows.length && <p className={styles.note}>暂无已保存消息。准备完成后可在下方说明目标。</p>}
      <Conversation messages={observation.rows.map(messageRow)} compacted={false} toolStates={liveTools(observation.snapshot?.live)}
        contentScope={`${api.projectId}:${sid}:${binding.binding_id}:${thread ?? ''}`}
        loadContent={(row, signal) => observation.content(raw.get(row.id) as CloudMessage, 'content', signal)}
        loadReasoning={async row => { const text = await observation.content(raw.get(row.id) as CloudMessage, 'reasoning');
          return { text: text.slice(row.reasoning?.length ?? 0), next_offset: [...text].length, complete: true }; }}
        history={{ enabled: true, hasMore: observation.hasMore, loading: observation.more, error: '', loadOlder: observation.loadOlder }} />
    </div>
    <footer className={styles.composer}>
      <label>发给此云端任务<textarea aria-label="发给此云端任务" value={draft.text} rows={3} disabled={!draft.ready}
        onChange={e => draft.setText(e.target.value)} placeholder="目标、必要背景与验收条件" /></label>
      <div className={styles.actions}><div className={styles.modelSelection}>
        <ModelMenu models={models?.model_ids ?? []} model={draft.model_id ?? ''} level={draft.thinking_level}
          options={models?.thinking_options[draft.model_id ?? '']} open={modelMenu} disabled={!draft.ready || !models}
          placement="above" toggle={() => setModelMenu(value => !value)} close={closeModels}
          selectModel={draft.setModel} selectThinking={draft.setThinking} />
      </div>
        <button disabled={!draft.ready || draft.saving || draft.conflict} onClick={() => void draft.save().catch(() => console.warn('cloud_draft_save_failed'))}>保存草稿</button>
        <button className={styles.primary} disabled={!metadataFresh || !draft.ready || draft.conflict || draft.saving || busy || sending || !canSend(binding.cloud) || !draft.text.trim() || !draft.model_id}
          onClick={() => void send()}>发送到云端</button></div>
      {draft.error && <p role="status" className={styles.error}>{draft.error}</p>}
      {modelsError && <p role="status" className={styles.error}>云端模型选项读取失败：{modelsError}<button className={styles.link} onClick={() => setRevision(v => v + 1)}>重试读取</button></p>}
      <small className={styles.note}>保存草稿到本机；未保存内容仅缓存在当前标签页。</small>
      {draft.conflict && <p role="status" className={styles.error}>草稿版本已变化，本页文本保留。<button className={styles.link} onClick={draft.reload}>重新载入已保存草稿</button></p>}
      {observation.usage && <small className={styles.note}>已记录 {observation.usage.recorded_calls} 次模型调用{observation.usage.capture.capture_incomplete ? ' · 用量记录不完整' : ''}</small>}
    </footer>
  </section>;
}
