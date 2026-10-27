import { useCallback, useEffect, useMemo, useState } from 'react';
import App from './App';
import { errorText, type WebApi } from './api';
import ProjectNavigation from './components/ProjectNavigation';
import type { ProjectPickerMode, ProjectRow, ScopeSelection } from './projectTypes';
import { useTheme } from './useTheme';
import { useSendScroll } from './useSendScroll';
import { sessionSyncPollMs } from './uiSettings';
import ProjectPicker from './components/ProjectPicker';
import { Chevron, Icon } from './components/Icons';
import styles from './components/ProjectNavigation.module.css';

/** Each visited scope retains its own fixed API and conversation controller. */
export default function ProjectWorkspace({ api }: { api: WebApi }) {
  const theme = useTheme(), sendScroll = useSendScroll();
  const [selection, setSelection] = useState<ScopeSelection>({ scopeId: api.scopeId, revision: 0 });
  const [visited, setVisited] = useState([api.scopeId]);
  const [catalog, setCatalog] = useState<ProjectRow[]>();
  const [failure, setFailure] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ [api.scopeId]: true });
  const [picker, setPicker] = useState<ProjectPickerMode>();
  const clients = useMemo(() => new Map([[api.scopeId, api]]), [api]);
  const select = useCallback((scopeId: string, sessionId?: string) => {
    setExpanded(values => ({ ...values, [scopeId]: true }));
    setVisited(previous => previous.includes(scopeId) ? previous : [...previous, scopeId]);
    setSelection(previous => ({ scopeId, sessionId, revision: previous.revision + 1 }));
  }, []);
  useEffect(() => {
    const controller = new AbortController(); setFailure('');
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        if (document.visibilityState === 'visible') {
          const result = await api.projects(controller.signal);
          if (!controller.signal.aborted) { setCatalog(result.items); setFailure(''); }
        }
      } catch (error) {
        if (!controller.signal.aborted) { console.warn('project_catalog_read_failed'); setFailure(errorText(error)); }
      } finally { if (!controller.signal.aborted) timer = setTimeout(read, sessionSyncPollMs); }
    }
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, refresh]);
  return <>{visited.map(scopeId => {
    let client = clients.get(scopeId);
    if (!client) { client = api.forScope(scopeId); clients.set(scopeId, client); }
    const project = catalog?.find(row => row.project_id === scopeId);
    const scopeName = scopeId === 'chats' ? '普通对话' : project?.name ?? '项目';
    const active = selection.scopeId === scopeId;
    return <div key={scopeId} hidden={!active}>
      <App api={client} active={active} selection={active ? selection : undefined} preferences={{ theme, sendScroll }}
        scopeName={scopeName}
        projectEntry={active ? path => <div className={styles.scope} role="status" aria-label="当前项目">
          <button type="button" className={styles.projectTrigger} title={path ? `${scopeName}\n${path}` : scopeName} aria-label={`项目与位置，当前：${scopeName}`}
            aria-haspopup="dialog" aria-expanded={picker === 'projects'} onClick={() => setPicker('projects')}><Icon name={scopeId === 'chats' ? 'chat' : 'folder'} />
            <strong>{scopeName}</strong><Chevron direction={picker === 'projects' ? 'up' : 'down'} /></button></div> : undefined}
        scopePath={project && 'path' in project.source ? project.source.path : undefined}
        navigation={current => <ProjectNavigation api={api} projects={catalog} failure={failure}
          revision={refresh}
          expanded={expanded} expand={(id, open) => setExpanded(values => ({ ...values, [id]: open }))}
          retry={() => setRefresh(value => value + 1)} scopeId={scopeId} select={select}>{current}</ProjectNavigation>} />
    </div>;
  })}{picker && <ProjectPicker api={api} mode={picker} projects={catalog} currentScope={selection.scopeId} failure={failure}
    retry={() => setRefresh(value => value + 1)} close={() => setPicker(undefined)}
    select={id => { select(id); setPicker(undefined); }} added={project => {
      setCatalog(rows => [...(rows ?? []).filter(row => row.project_id !== project.project_id), project].sort((a, b) => a.name.localeCompare(b.name)));
      setRefresh(value => value + 1); select(project.project_id); setPicker(undefined);
    }} />}</>;
}
