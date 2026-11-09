import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding } from '../cloudTypes';
import { cloudError } from '../cloudPresentation';
import { keepDialogFocus } from '../dialogFocus';
import styles from './CloudTasks.module.css';

export default function CloudTaskForget({ api, sid, binding, onForgotten }: {
  api: CloudApi; sid: string; binding: CloudBinding; onForgotten?: () => void;
}) {
  const [intent, setIntent] = useState<{ command_id: string; expected_binding_revision: number }>();
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const pending = useRef(false); const alive = useRef(true);
  const dialog = useRef<HTMLDialogElement>(null); const button = useRef<HTMLButtonElement>(null);
  const title = useId();
  useLayoutEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { if (intent) dialog.current?.showModal(); }, [intent]);
  function close() {
    if (pending.current) return;
    dialog.current?.close(); setIntent(undefined); setError(''); button.current?.focus();
  }
  async function remove() {
    if (!intent || pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try {
      const value = await api.forgetBinding(sid, binding.binding_id, { ...intent, acknowledge_unmanaged: true });
      if (value.binding_id !== binding.binding_id || value.local_session_id !== sid || value.retained)
        throw new Error('cloud_task_identity_mismatch');
      if (alive.current) {
        dialog.current?.close(); setIntent(undefined); onForgotten?.();
      }
    } catch (failure) {
      console.warn('cloud_task_forget_failed');
      if (alive.current) setError(cloudError(failure));
    } finally {
      pending.current = false; if (alive.current) setBusy(false);
    }
  }
  return <>
    <button ref={button} className={styles.danger} disabled={busy} onClick={() => {
      setError(''); setIntent({ command_id: crypto.randomUUID(), expected_binding_revision: binding.revision });
    }}>移除本机关联</button>
    {intent && <dialog ref={dialog} className={styles.dialog} aria-labelledby={title}
      onCancel={event => { event.preventDefault(); close(); }}
      onKeyDown={event => {
        keepDialogFocus(event, dialog.current);
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
      }}>
      <h3 id={title}>移除此任务的本机关联？</h3>
      <p>任务 {binding.cloud?.title || binding.task_id?.slice(0, 8) || '准备中'} · 连接 {binding.profile_id}</p>
      <p>确认后，此任务将从本机界面消失，本机将放弃管理它。主聊天、已下载文件和已接收材料保留。</p>
      <p>远端任务如果仍然存在，可能继续运行，需要在开发机上另行处理。</p>
      {error && <p role="alert" className={styles.error}>{error}</p>}
      <div className={styles.actions}>
        <button disabled={busy} onClick={close}>取消</button>
        <button className={styles.danger} disabled={busy} onClick={() => void remove()}>{busy ? '正在移除…' : '确认移除'}</button>
      </div>
    </dialog>}
  </>;
}
