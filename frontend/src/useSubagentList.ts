import { useCallback, useEffect, useMemo, useState } from 'react';
import { errorText, type WebApi } from './api';
import type { RunView, SubagentRecord } from './types';

export function useSubagentList(api: WebApi, sessionId?: string, run?: RunView | null) {
  const [saved, setSaved] = useState<{ api: WebApi; sid: string; rows: SubagentRecord[] }>();
  const [failure, setFailure] = useState<{ api: WebApi; sid: string; text: string }>();
  const [pending, setPending] = useState<string>();
  const [revision, setRevision] = useState(0);
  const currentRun = run?.session_id === sessionId ? run : undefined;
  useEffect(() => {
    if (!sessionId) return;
    const controller = new AbortController();
    setPending(sessionId); setFailure(undefined);
    api.subagents(sessionId, controller.signal).then(rows => {
      if (!controller.signal.aborted) setSaved({ api, sid: sessionId, rows });
    }).catch(error => {
      if (!controller.signal.aborted) {
        console.warn('subagent_list_read_failed');
        setFailure({ api, sid: sessionId, text: errorText(error) });
      }
    }).finally(() => { if (!controller.signal.aborted) setPending(undefined); });
    return () => controller.abort();
  }, [api, sessionId, currentRun?.run_id, currentRun?.done, revision]);
  const records = useMemo(() => {
    const rows = new Map((saved?.api === api && saved.sid === sessionId ? saved.rows : []).map(row => [row.agent_path, row]));
    for (const agent of Object.values(currentRun?.agents ?? {})) {
      const source = agent.source;
      const parent = [...currentRun!.messages].reverse().find(row =>
        (!agent.parent_batch_id || row.id === agent.parent_batch_id) &&
        (!row.run_id || row.run_id === source.parent_run_id) &&
        row.tool_calls?.some(call => call.id === source.parent_tool_call_id));
      const call = parent?.tool_calls?.find(item => item.id === source.parent_tool_call_id);
      const old = rows.get(source.agent_path);
      const active = ['running', 'stopping', 'awaiting_approval', 'interrupted'].includes(agent.status);
      rows.set(source.agent_path, { ...old, agent_id: source.agent_id, agent_path: source.agent_path,
        agent: source.agent_path.split('/')[1], task: String(call?.args.task ?? old?.task ?? ''),
        ...(active ? { resume_available: false, resume_reason: agent.status } : {}),
        tool_call_id: source.parent_tool_call_id, parent_batch_id: agent.parent_batch_id, status: agent.status });
    }
    return [...rows.values()];
  }, [api, saved, sessionId, currentRun]);
  const retry = useCallback(() => setRevision(value => value + 1), []);
  return { records, loading: !!sessionId && pending === sessionId, error: failure?.api === api && failure.sid === sessionId ? failure.text : '', retry };
}

export type SubagentList = ReturnType<typeof useSubagentList>;
