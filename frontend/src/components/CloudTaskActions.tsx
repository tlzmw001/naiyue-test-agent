import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding, DeletePreview } from '../cloudTypes';
import { canSend, cloudError } from '../cloudPresentation';
import styles from './CloudTasks.module.css';

export type CloudAction = (kind: 'control' | 'environment' | 'delete', body: Record<string, unknown>) => Promise<void>;
export default function CloudTaskActions({ api, sid, binding, busy, execute, forceReleaseAllowed = true }: {
  api: CloudApi; sid: string; binding: CloudBinding; busy: boolean; execute: CloudAction; forceReleaseAllowed?: boolean;
}) {
  const [preview, setPreview] = useState<DeletePreview>(); const [checking, setChecking] = useState(false);
  const [acknowledged, acknowledge] = useState(false); const [error, setError] = useState('');
  const [release, setRelease] = useState<{ revision: number; generation: number }>();
  const dialog = useRef<HTMLDialogElement>(null); const button = useRef<HTMLButtonElement>(null);
  const task = binding.cloud;
  useEffect(() => { if (preview || release) dialog.current?.showModal(); }, [preview, release]);
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
  function close() { dialog.current?.close(); setPreview(undefined); setRelease(undefined); button.current?.focus(); }
  return <>
    <div className={styles.actions} aria-label="云端任务控制">
      <button disabled={busy || !task.active_run_id && !task.reclaim_blockers.includes('background_commands')}
        onClick={() => void execute('control', { ...common, kind: 'stop', run_id: task.active_run_id ?? task.last_run_id, checkpoint_id: null })}>停止本轮</button>
      {task.environment_phase === 'absent' ? <button disabled={busy || !!task.reclaim_blockers.length}
        onClick={() => void execute('environment', { ...common, action: 'start', acknowledge_unknown: false, expected_generation: null })}>启动原环境</button> :
        <button disabled={busy || !quiescent} onClick={() => void execute('environment', { ...common, action: 'release', acknowledge_unknown: false, expected_generation: null })}>释放环境</button>}
      {task.runner_recovery && <button disabled={busy || !!task.artifact_operation_id} onClick={() => void execute('control', { ...common, kind: 'recover',
        run_id: task.runner_recovery!.run_id, checkpoint_id: task.runner_recovery!.checkpoint_id ?? null })}>核对恢复</button>}
      {uncertain && task.environment_phase !== 'absent' && <button disabled={busy || !forceReleaseAllowed || !!task.active_run_id || !!task.runner_recovery}
        onClick={() => { acknowledge(false); setRelease({ revision: binding.revision, generation: task.generation }); }}>核对并释放环境</button>}
      <button ref={button} className={styles.danger} disabled={busy || checking || task.lifecycle === 'deleting'} onClick={() => void prepareDelete()}>删除任务</button>
    </div>
    {uncertain && !forceReleaseAllowed && <p className={styles.note}>存在尚未核对的原操作，请先核对原回执，再决定是否释放；不会重放未知执行。</p>}
    {!canSend(task) && <p className={styles.note}>{task.active_run_id ? '本轮执行中，可继续编辑草稿；结束或停止并保存后再发送。' :
      task.environment_phase === 'absent' ? '环境已释放，启动后继续使用原工作区。' : '任务正在准备、保存或等待核对，暂不可发送。'}</p>}
    {error && <p role="alert" className={styles.error}>{error}</p>}
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
      <h3 id="cloud-delete-title">删除此云端任务？</h3>
      <p>任务 {preview.task_id.slice(0, 8)} · 连接 {binding.profile_id}</p>
      <p>将删除云端工作区、执行历史和产物，无法恢复。主聊天和已经下载的文件保留。</p>
      {preview.requires_stop && !uncertain && <p role="status">请先停止本轮并等待保存完成，再重新预览删除。</p>}
      {preview.requires_stop && uncertain && <label className={styles.check}><input type="checkbox" checked={acknowledged}
        onChange={e => acknowledge(e.target.checked)} />我确认放弃尚未保存的现场，仍需核验原容器已停止</label>}
      <div className={styles.actions}><button onClick={close}>取消</button>
        <button className={styles.danger} disabled={busy || preview.requires_stop && (!uncertain || !acknowledged)} onClick={async () => {
          await execute('delete', { ...common, expected_revision: preview.revision, expected_generation: preview.generation,
            preview_digest: preview.preview_digest, acknowledge_unknown: acknowledged }); close();
        }}>确认删除任务</button></div>
    </dialog>}
  </>;
}
