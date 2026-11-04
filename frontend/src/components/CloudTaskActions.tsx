import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding, DeletePreview } from '../cloudTypes';
import { canSend, cloudError } from '../cloudPresentation';
import styles from './CloudTasks.module.css';

export type CloudAction = (kind: 'control' | 'environment' | 'delete', body: Record<string, unknown>) => Promise<boolean | void>;
export default function CloudTaskActions({ api, sid, binding, busy, execute, forceReleaseAllowed = true }: {
  api: CloudApi; sid: string; binding: CloudBinding; busy: boolean; execute: CloudAction; forceReleaseAllowed?: boolean;
}) {
  const [preview, setPreview] = useState<DeletePreview>(); const [checking, setChecking] = useState(false);
  const [acknowledged, acknowledge] = useState(false); const [error, setError] = useState('');
  const [stop, setStop] = useState<{ revision: number; run: string | null }>();
  const [release, setRelease] = useState<{ revision: number; generation: number }>();
  const dialog = useRef<HTMLDialogElement>(null); const button = useRef<HTMLButtonElement>(null);
  const task = binding.cloud;
  useEffect(() => { if (preview || release || stop) dialog.current?.showModal(); }, [preview, release, stop]);
  if (!task) return null;
  const quiescent = !task.active_run_id && !task.reclaim_blockers.length && !task.environment_operation_id &&
    !task.control_operation_id && !task.artifact_operation_id && task.last_quiescent_at !== null;
  const uncertain = ['reconciling', 'failed'].includes(task.lifecycle) || ['disconnected', 'blocked'].includes(task.runner_phase) ||
    task.environment_phase === 'unknown' || !!task.environment_operation_id || task.reclaim_blockers.some(v => v.includes('unknown'));
  const common = { expected_binding_revision: binding.revision };
  async function prepareDelete() {
    setChecking(true); setError(''); acknowledge(false);
    try { setPreview(await api.preview(sid, binding.binding_id)); }
    catch (failure) { console.warn('cloud_delete_preview_failed'); setError(cloudError(failure)); }
    finally { setChecking(false); }
  }
  function close() { dialog.current?.close(); setPreview(undefined); setRelease(undefined); setStop(undefined); button.current?.focus(); }
  return <>
    <div className={styles.actions} aria-label="云端任务控制">
      <button disabled={busy || !task.active_run_id && !task.reclaim_blockers.includes('background_commands')}
        onClick={() => { if (!task.active_run_id) { setError(''); setStop({ revision: binding.revision, run: task.last_run_id }); } else void execute('control', { ...common, kind: 'stop', run_id: task.active_run_id, checkpoint_id: null }); }}>{task.active_run_id ? '停止本轮' : task.reclaim_blockers.includes('background_commands') ? '停止后台任务' : '停止本轮'}</button>
      {task.environment_phase === 'absent' ? <button disabled={busy || !!task.reclaim_blockers.length}
        onClick={() => void execute('environment', { ...common, action: 'start', acknowledge_unknown: false, expected_generation: null })}>启动原环境</button> :
        <button disabled={busy || !quiescent} onClick={() => void execute('environment', { ...common, action: 'release', acknowledge_unknown: false, expected_generation: null })}>释放环境</button>}
      {task.runner_recovery && <button disabled={busy || !!task.artifact_operation_id} onClick={() => void execute('control', { ...common, kind: 'recover',
        run_id: task.runner_recovery!.run_id, checkpoint_id: task.runner_recovery!.checkpoint_id ?? null })}>核对恢复</button>}
      {uncertain && task.environment_phase !== 'absent' && <button disabled={busy || !forceReleaseAllowed || !!task.active_run_id || !!task.runner_recovery}
        onClick={() => { acknowledge(false); setRelease({ revision: binding.revision, generation: task.generation }); }}>核对并释放环境</button>}
      <button ref={button} className={`${styles.danger} ${styles.deleteTask}`} disabled={busy || checking || task.lifecycle === 'deleting'} onClick={() => void prepareDelete()}>删除任务</button>
    </div>
    {uncertain && !forceReleaseAllowed && <p className={styles.note}>存在尚未核对的原操作，请先核对原回执，再决定是否释放；不会重放未知执行。</p>}
    {!canSend(task) && <p className={styles.note}>{task.active_run_id ? '本轮执行中，可继续编辑草稿；结束或停止并保存后再发送。' :
      task.environment_phase === 'absent' ? '环境已释放，启动后继续使用原工作区。' : '任务正在准备、保存或等待核对，暂不可发送。'}</p>}
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {stop && <dialog ref={dialog} className={styles.dialog} aria-labelledby="cloud-stop-title" onCancel={e => { e.preventDefault(); close(); }}>
      <h3 id="cloud-stop-title">停止此任务的后台服务？</h3>
      <p>任务 {task.title || binding.task_id?.slice(0, 8)} · 连接 {binding.profile_id}</p>
      <p>将终止此云端任务中仍在运行的全部后台命令和服务，包括前面轮次启动并保留的服务。正在服务的请求会中断。</p>
      <p>当前接口不提供后台进程明细；不会停止其他云端任务的服务。</p>
      {(stop.revision !== binding.revision || !!task.active_run_id) && <p role="alert">任务状态已变化，请取消后重新核对。</p>}
      <div className={styles.actions}><button onClick={close}>取消</button><button disabled={busy || stop.revision !== binding.revision || !!task.active_run_id} onClick={async () => {
        const accepted = await execute('control', { expected_binding_revision: stop.revision, kind: 'stop', run_id: stop.run, checkpoint_id: null });
        if (accepted !== false) close();
      }}>确认停止后台任务</button></div>
    </dialog>}
    {release && <dialog ref={dialog} className={styles.dialog} aria-labelledby="cloud-release-title" onCancel={e => { e.preventDefault(); close(); }}>
      <h3 id="cloud-release-title">释放状态未确认的环境？</h3>
      <p>任务 {binding.task_id?.slice(0, 8)} · 连接 {binding.profile_id}</p>
      <p>将核对并停止原容器。工作区和已保存历史保留；尚未保存的执行现场可能丢失。完成后可启动原环境。</p>
      <label className={styles.check}><input type="checkbox" checked={acknowledged} onChange={e => acknowledge(e.target.checked)} />我确认放弃尚未保存的现场</label>
      <div className={styles.actions}><button onClick={close}>取消</button><button disabled={busy || !acknowledged} onClick={async () => {
        await execute('environment', { action: 'release', expected_binding_revision: release.revision, expected_generation: release.generation, acknowledge_unknown: true }); close();
      }}>确认释放此环境</button></div>
    </dialog>}
    {preview && <dialog ref={dialog} className={styles.dialog} aria-labelledby="cloud-delete-title" onCancel={e => { e.preventDefault(); close(); }}>
      <h3 id="cloud-delete-title">删除此云端任务？</h3>{error && <p role="alert" className={styles.error}>{error}</p>}
      <p>任务 {preview.task_id.slice(0, 8)} · 连接 {binding.profile_id}</p>
      <p>将删除云端工作区、执行历史和产物，无法恢复。主聊天和已经下载的文件保留。</p>
      {preview.requires_stop && !uncertain && <p role="status">请先停止本轮并等待保存完成，再重新预览删除。</p>}
      {preview.requires_stop && uncertain && <label className={styles.check}><input type="checkbox" checked={acknowledged}
        onChange={e => acknowledge(e.target.checked)} />我确认放弃尚未保存的现场，仍需核验原容器已停止</label>}
      <div className={styles.actions}><button onClick={close}>取消</button>
        <button className={styles.danger} disabled={busy || preview.requires_stop && (!uncertain || !acknowledged)} onClick={async () => {
          const accepted = await execute('delete', { ...common, expected_revision: preview.revision, expected_generation: preview.generation,
            preview_digest: preview.preview_digest, acknowledge_unknown: acknowledged });
          if (accepted !== false) close(); else setError('删除未被受理。请取消后刷新任务，再重新预览删除。');
        }}>确认删除任务</button></div>
    </dialog>}
  </>;
}
