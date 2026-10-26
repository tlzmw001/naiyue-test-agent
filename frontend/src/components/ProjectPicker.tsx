import { useEffect, useRef, useState } from 'react';
import { ApiError, errorText, type WebApi } from '../api';
import type { FolderChoice, ProjectPickerMode, ProjectRow, RegistrationOperation } from '../projectTypes';
import DirectoryBrowser from './DirectoryBrowser';
import { displayProjectPath } from '../projectPaths';
import { keepDialogFocus } from '../dialogFocus';
import { Icon } from './Icons';
import styles from './ProjectPicker.module.css';

export default function ProjectPicker({ api, mode, projects, currentScope, failure, retry, close, added, select }: {
  api: WebApi; mode: ProjectPickerMode; projects?: ProjectRow[]; currentScope: string; failure?: string; retry?: () => void;
  close: () => void; added: (project: ProjectRow) => void; select: (scopeId: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null), inFlight = useRef(false);
  const [view, setView] = useState(mode);
  const [choice, setChoice] = useState<(FolderChoice & { requestId: string })>();
  const [browsePath, setBrowsePath] = useState<string>(), [folderName, setFolderName] = useState('');
  const [trusted, setTrusted] = useState(false), [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(''), [attempted, setAttempted] = useState(false);
  const [operation, setOperation] = useState<RegistrationOperation>();
  useEffect(() => {
    const node = dialog.current!, origin = document.activeElement as HTMLElement | null;
    node.showModal();
    return () => { node.close(); if (origin?.isConnected) origin.focus(); };
  }, []);
  async function readResult() {
    if (!choice) return;
    const result = (await api.projectOperations()).items.find(row => row.operation_id === choice.requestId);
    setOperation(result);
    if (result?.stage === 'done') added(await api.getProject(result.project_id));
    else setNotice(result ? '登记尚未完成，可以继续完成这次登记；原文件夹和操作记录会保留。' : '尚未找到这次登记记录，可核对连接后使用同一请求重试。');
  }
  async function perform(action: 'commit' | 'check' | 'resume') {
    if (inFlight.current || !choice || (action === 'commit' && !trusted)) return;
    inFlight.current = true; setBusy(true); setNotice('');
    try {
      if (action === 'check') await readResult();
      else if (action === 'resume') {
        const result = await api.reconcileProject(choice.requestId);
        const failure = result.items.find(row => row.operation_id === choice.requestId)?.error;
        if (failure) throw new ApiError(failure);
        await readResult();
      } else {
        setAttempted(true);
        added(choice.mode === 'create' ? await api.createLocalProject(choice.path, choice.folderName!, choice.identity, choice.requestId) :
          await api.addLocalProject(choice.path, choice.identity, choice.requestId));
      }
    } catch (error) { console.warn('project_registration_unconfirmed'); setNotice(errorText(error)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  const title = choice ? '确认项目文件夹' : view === 'projects' ? '项目与位置' : view === 'create' ? '创建项目文件夹' : '选择项目文件夹';
  return <dialog ref={dialog} className={styles.dialog} data-view={!choice ? view : 'confirmation'} aria-label={title} onKeyDown={event => keepDialogFocus(event, dialog.current)}
    onCancel={event => { event.preventDefault(); if (!busy) close(); }}>
    <header className={styles.header}><div><h2>{title}</h2><p>此计算机</p></div><button type="button" aria-label="关闭项目选择" disabled={busy} onClick={close}><Icon name="close" /></button></header>
    {choice ? <section className={styles.confirmation}>
      <p>{choice.mode === 'create' ? '将在以下位置创建文件夹并添加项目：' : '将以下文件夹添加为项目：'}</p>
      <code className={styles.target} dir="ltr">{displayProjectPath(choice.target)}</code>
      <p>添加后，Agent 可以读取目录中的项目指令。文件和命令操作仍遵循当前权限模式与审批。</p>
      <label className={styles.trust}><input type="checkbox" checked={trusted} disabled={busy} onChange={event => setTrusted(event.target.checked)} />我信任这个文件夹</label>
      {notice && <p className={styles.error} role="status">{notice}</p>}
      {attempted && <div className={styles.recovery}><button type="button" disabled={busy} onClick={() => void perform('check')}>核对操作结果</button>
        {operation && operation.stage !== 'done' && <button type="button" disabled={busy} onClick={() => void perform('resume')}>继续完成登记</button>}</div>}
      <div className={styles.actions}><button type="button" disabled={busy} onClick={() => { setBrowsePath(choice.path); setFolderName(choice.folderName ?? ''); setChoice(undefined); setTrusted(false); setNotice(''); setAttempted(false); setOperation(undefined); }}>返回目录</button>
        <button type="button" disabled={busy} onClick={close}>取消</button>
        <button type="button" className={styles.primary} disabled={!trusted || busy} onClick={() => void perform('commit')}>
          {busy ? '正在处理…' : attempted ? '重试添加项目' : choice.mode === 'create' ? '创建并添加项目' : '信任并添加项目'}</button></div>
    </section> : view === 'projects' ? <section className={styles.projectList}>
      <p className={styles.hint}>打开其他项目会离开当前会话，不会改变该会话的归属。</p>
      {retry && <button type="button" aria-label="刷新项目目录" onClick={retry}>刷新目录</button>}
      <button type="button" aria-label="打开普通对话" aria-current={currentScope === 'chats' ? 'true' : undefined} onClick={() => select('chats')}><Icon name="chat" /><span>普通对话</span></button>
      {failure ? <p className={styles.error} role="status">{failure}<button type="button" onClick={retry}>重试项目目录</button></p> :
        !projects ? <p role="status">正在读取项目…</p> : !projects.length && <p className={styles.hint}>还没有项目</p>}
      {projects?.map(project => <button type="button" key={project.project_id} aria-label={`打开${project.name}`}
        aria-current={currentScope === project.project_id ? 'true' : undefined} onClick={() => select(project.project_id)}><Icon name="folder" /><span>{project.name}</span>{!project.trusted && <small>未信任</small>}</button>)}
      <div className={styles.addActions}><button type="button" onClick={() => setView('select')}><Icon name="folder" />选择已有文件夹</button>
        <button type="button" onClick={() => setView('create')}><Icon name="plus" />创建新文件夹</button></div>
    </section> : <><div className={styles.modes} role="group" aria-label="添加项目方式">
      <button type="button" aria-pressed={view === 'select'} onClick={() => setView('select')}>选择已有文件夹</button>
      <button type="button" aria-pressed={view === 'create'} onClick={() => setView('create')}>创建新文件夹</button></div>
      <DirectoryBrowser api={api} mode={view} initialPath={browsePath} initialName={folderName} close={close}
        choose={value => { setChoice({ ...value, requestId: crypto.randomUUID() }); setNotice(''); setAttempted(false); setOperation(undefined); }} />
    </>}
  </dialog>;
}
