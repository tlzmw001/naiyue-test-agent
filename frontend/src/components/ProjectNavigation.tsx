import { useEffect, useId, useState, type ReactNode } from 'react';
import { errorText, type WebApi } from '../api';
import type { ProjectRow } from '../projectTypes';
import type { SessionRow } from '../types';
import { Chevron, Icon } from './Icons';
import SessionIndicators from './SessionIndicators';
import { sessionSyncPollMs } from '../uiSettings';
import styles from './ProjectNavigation.module.css';

function status(project: ProjectRow) {
  const values = [];
  if (project.lifecycle !== 'active') values.push('项目处理中');
  if (!project.trusted) values.push('未信任');
  const availability: Record<string, string> = { missing: '目录不存在', not_directory: '路径不是目录',
    unreadable: '无法访问目录', unavailable: '执行不可用', remote: '远端项目' };
  if (project.availability && project.availability !== 'available') values.push(availability[project.availability] ?? '执行不可用');
  return values.join(' · ');
}
function Directory({ api, scopeId, select, refresh }: { api: WebApi; scopeId: string; refresh: number; select: (scopeId: string, sessionId?: string) => void }) {
  const listId = useId();
  const [rows, setRows] = useState<SessionRow[]>();
  const [failure, setFailure] = useState('');
  const [revision, retry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        if (document.visibilityState === 'visible') {
          const value = await api.directory(scopeId, controller.signal);
          if (!controller.signal.aborted) { setRows(value); setFailure(''); }
        }
      } catch (error) {
        if (!controller.signal.aborted) { console.warn('project_session_directory_failed'); setFailure(errorText(error)); }
      } finally { if (!controller.signal.aborted) timer = setTimeout(read, sessionSyncPollMs); }
    }
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, scopeId, revision, refresh]);
  if (failure) return <p className={styles.hint} role="status">{failure} <button onClick={() => retry(value => value + 1)} aria-label="重试会话目录">重试</button></p>;
  if (!rows) return <p className={styles.hint} role="status">正在读取会话…</p>;
  if (!rows.length) return <p className={styles.hint}>还没有历史会话</p>;
  return <div data-session-scope={scopeId}>{rows.map(row => <button key={row.session_id} className={styles.session} data-session-id={row.session_id} onClick={() => select(scopeId, row.session_id)}
    aria-describedby={`${listId}-${row.session_id}-facts`}
    aria-label={`${row.title ?? '未命名会话'} ${row.model_id}`} title={`${row.title ?? row.session_id} · ${row.model_id}`}>
    {row.pinned ? <span className={styles.pin} role="img" aria-label="已置顶" title="已置顶"><Icon name="pin" /></span> : <Icon name="chat" />}
    <span className={styles.summary}><span className={styles.sessionTitle}>{row.title ?? '未命名会话'}</span>
      <SessionIndicators id={`${listId}-${row.session_id}-facts`} row={row} />
    </span>
  </button>)}</div>;
}
export default function ProjectNavigation({ api, projects, failure, retry, scopeId, select, children, expanded, expand, revision }: {
  api: WebApi; projects?: ProjectRow[]; failure: string; retry: () => void; scopeId: string;
  select: (scopeId: string, sessionId?: string) => void; children: ReactNode;
  expanded: Record<string, boolean>; expand: (id: string, open: boolean) => void;
  revision: number;
}) {
  const group = (id: string, name: string, project?: ProjectRow) => {
    const open = expanded[id] ?? id === scopeId;
    return <section key={id} role="group" aria-label={name} className={styles.group}>
      <div className={styles.heading} data-selected={id === scopeId}>
        <button className={styles.expand} aria-label={`${open ? '收起' : '展开'}${name}`} aria-expanded={open}
          onClick={() => expand(id, !open)}><Chevron direction={open ? 'down' : 'right'} /></button>
        <button className={styles.enter} aria-current={id === scopeId ? 'location' : undefined}
          aria-label={`进入${name}`} onClick={() => { expand(id, true); select(id); }}>
          <Icon name={id === 'chats' ? 'chat' : 'folder'} /><span>{name}</span>
        </button>
      </div>
      {project && status(project) && <p className={styles.status}>{status(project)}</p>}
      {open && <div className={styles.children}>{id === scopeId ? children : <Directory api={api} scopeId={id} select={select} refresh={revision} />}</div>}
    </section>;
  };
  return <>{group('chats', '普通对话')}
    {failure ? <p className={styles.hint} role="status">{failure} <button onClick={retry}>重试项目目录</button></p> :
      !projects && <p className={styles.hint} role="status">正在读取项目…</p>}
    {projects?.map(project => group(project.project_id, project.name, project))}
    {scopeId !== 'chats' && projects && !projects.some(project => project.project_id === scopeId) &&
      <section role="group" aria-label="当前项目"><p className={styles.hint}>当前项目不在目录中，请刷新核对。</p>{children}</section>}
  </>;
}
