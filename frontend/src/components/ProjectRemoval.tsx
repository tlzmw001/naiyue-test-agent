import { useEffect, useRef, useState } from 'react';
import { ApiError, errorText, type WebApi } from '../api';
import type { ProjectRow, RegistrationOperation } from '../projectTypes';
import { keepDialogFocus } from '../dialogFocus';
import { displayProjectPath } from '../projectPaths';
import styles from './ProjectPicker.module.css';

export default function ProjectRemoval({ api, project, close, removed }: {
  api: WebApi; project: ProjectRow; close: () => void; removed: (id: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null), inFlight = useRef(false);
  const requestId = useRef<string>(crypto.randomUUID()), rejected = useRef(false);
  const [busy, setBusy] = useState(false), [attempted, setAttempted] = useState(false), [notice, setNotice] = useState('');
  useEffect(() => {
    const node = dialog.current!, origin = document.activeElement as HTMLElement | null;
    node.showModal();
    return () => { node.close(); if (origin?.isConnected) origin.focus(); };
  }, []);
  function completed(value: { project_id: string; data_retained?: boolean } | null | undefined) {
    if (value?.project_id !== project.project_id || value.data_retained !== true) return false;
    removed(project.project_id); return true;
  }
  async function operation(): Promise<RegistrationOperation | undefined> {
    const { items } = await api.projectOperations();
    const matching = items.find(row => row.operation_id === requestId.current);
    if (matching && (matching.action !== 'remove' || matching.project_id !== project.project_id)) throw new ApiError('project_operation_conflict');
    return matching ?? items.find(row => row.action === 'remove' && row.project_id === project.project_id && row.stage !== 'done');
  }
  async function perform(checkOnly = false) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setNotice('');
    try {
      const saved = await operation();
      if (saved) {
        requestId.current = saved.operation_id;
        if (saved.stage === 'done') {
          if (!completed(saved.result)) throw new Error('unconfirmed_removal_receipt');
          return;
        }
      }
      if (checkOnly) {
        setNotice(saved ? '移除尚未完成，可以重试以继续完成原操作。' : '尚未确认移除结果。可以重试，系统会核对原请求。');
        return;
      }
      setAttempted(true);
      if (saved) {
        const result = await api.reconcileProject(requestId.current);
        const failure = result.items.find(row => row.operation_id === requestId.current)?.error;
        if (failure) throw new ApiError(failure);
        const current = await operation();
        if (current?.stage !== 'done' || !completed(current.result)) throw new Error('unconfirmed_removal_receipt');
      } else {
        if (rejected.current) requestId.current = crypto.randomUUID();
        rejected.current = false;
        if (!completed(await api.removeProject(project.project_id, requestId.current))) throw new Error('unconfirmed_removal_receipt');
      }
    } catch (error) {
      console.warn('project_removal_unconfirmed');
      rejected.current = error instanceof ApiError && ['project_in_use', 'project_cloud_tasks_retained', 'project_operation_pending'].includes(error.code);
      setNotice(error instanceof ApiError && error.code !== 'project_operation_failed' ? errorText(error) :
        '尚未确认项目是否已移除，请核对操作结果或重试。磁盘文件和本地历史数据将保留。');
    } finally { inFlight.current = false; setBusy(false); }
  }
  return <dialog ref={dialog} className={styles.dialog} data-view="projects" aria-label="移除项目"
    onKeyDown={event => keepDialogFocus(event, dialog.current)} onCancel={event => { event.preventDefault(); if (!busy) close(); }}>
    <header className={styles.header}><h2>移除项目</h2></header>
    <section className={styles.confirmation}>
      <p>从项目列表移除“{project.name}”？</p>
      {'path' in project.source && <code className={styles.target} dir="ltr">{displayProjectPath(project.source.path)}</code>}
      <p>磁盘文件和本地历史数据将保留。移除后，该项目及其会话将不再显示在列表中。</p>
      {project.availability === 'missing' && <p>原项目目录已不存在，仍可移除登记。</p>}
      {notice && <p className={styles.error} role="status">{notice}</p>}
      <div className={styles.actions}>
        <button type="button" disabled={busy} onClick={close}>取消</button>
        {attempted && <button type="button" disabled={busy} onClick={() => void perform(true)}>核对操作结果</button>}
        <button type="button" className={styles.primary} disabled={busy} onClick={() => void perform()}>
          {busy ? '正在处理…' : attempted ? '重试移除' : '确认移除'}
        </button>
      </div>
    </section>
  </dialog>;
}
