import { useEffect, useState } from 'react';
import type { ServiceInfo, WebApi } from '../api';
import type { SessionView } from '../types';
import styles from '../App.module.css';

function pathLabel(path: string) {
  const parts = path.split(/[\\/]/).filter(Boolean);
  const root = !parts.length || (parts.length === 1 && /^[a-z]:$/i.test(parts[0])) ||
    (/^[\\/]{2}/.test(path) && parts.length === 2);
  return root ? path : parts.at(-1)!;
}

export default function WorkspaceLabel({ api, session }: {
  api: WebApi; session?: Pick<SessionView, 'workspace_root'> | null;
}) {
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ api: WebApi; revision: number; value?: ServiceInfo; failed?: boolean }>();
  const current = result?.api === api && result.revision === revision ? result : undefined;

  useEffect(() => {
    const controller = new AbortController();
    void api.serviceInfo(controller.signal).then(value => {
      if (!controller.signal.aborted) setResult({ api, revision, value });
    }, () => {
      if (!controller.signal.aborted) {
        console.warn('workspace_info_read_failed');
        setResult({ api, revision, failed: true });
      }
    });
    return () => controller.abort();
  }, [api, revision]);

  const path = session ? session.workspace_root : current?.value?.workspace_root;
  const label = session ? (path ? pathLabel(path) : '未绑定工作区') : current?.value ?
    (current.value.workspace_label ?? '未绑定工作区') : current?.failed ? '工作区信息读取失败' : '正在读取工作区';

  return <div className={styles.workspacePath} role="status" aria-label="当前工作区">
    <span className={styles.workspaceName} title={path ?? undefined}>{label}</span>
    {!session && current?.failed && <button type="button" className={styles.workspaceRetry}
      aria-label="重试工作区信息" onClick={() => setRevision(value => value + 1)}>重试</button>}
  </div>;
}
