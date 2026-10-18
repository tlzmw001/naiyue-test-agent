import { useEffect, useMemo, useState } from 'react';
import { errorText, WebApi } from './api';
import { useMessageHistory } from './useMessageHistory';
import type { RunView, SubagentDetail, SubagentRecord } from './types';

export function useSubagents(api: WebApi, sessionId: string, run?: RunView | null, initialAgentId?: string) {
  const history = useMessageHistory(api);
  const [saved, setSaved] = useState<{ sid: string; rows: SubagentRecord[] }>();
  const [selection, setSelection] = useState<{ sid: string; id: string }>();
  const [detail, setDetail] = useState<{ sid: string; value: SubagentDetail }>();
  const [listError, setListError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [revision, setRevision] = useState(0);
  const selected = selection?.sid === sessionId ? selection.id : undefined;
  const currentRun = run?.session_id === sessionId ? run : undefined;
  const selectedLive = Object.values(currentRun?.agents ?? {}).find(agent => agent.source.agent_id === selected);
  const hasLiveMessages = selectedLive?.messages !== undefined;

  useEffect(() => {
    setSelection(initialAgentId ? { sid: sessionId, id: initialAgentId } : undefined);
    setDetail(undefined);
    history.reset();
  }, [sessionId, initialAgentId]);

  useEffect(() => {
    let current = true;
    setLoading(true); setListError('');
    api.subagents(sessionId).then(rows => {
      if (current) setSaved({ sid: sessionId, rows });
    }).catch(error => { if (current) setListError(errorText(error)); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [api, sessionId, currentRun?.run_id, currentRun?.done, revision]);

  useEffect(() => {
    if (!selected) return;
    let current = true;
    setLoadingDetail(true); setDetailError('');
    api.subagent(sessionId, selected).then(value => {
      if (current) {
        setDetail({ sid: sessionId, value });
        if (!history.sessionId) history.open(sessionId, selected, value);
        else void history.synchronize(value);
      }
    }).catch(error => { if (current) setDetailError(errorText(error)); })
      .finally(() => { if (current) setLoadingDetail(false); });
    return () => { current = false; };
  }, [api, sessionId, selected, currentRun?.done, selectedLive?.status, selectedLive?.snapshot_sequence, hasLiveMessages, revision]);

  const records = useMemo(() => {
    const rows = new Map((saved?.sid === sessionId ? saved.rows : []).map(row => [row.agent_path, row]));
    for (const agent of Object.values(currentRun?.agents ?? {})) {
      const source = agent.source;
      const parent = [...currentRun!.messages].reverse().find(row =>
        (!agent.parent_batch_id || row.id === agent.parent_batch_id) &&
        (!row.run_id || row.run_id === source.parent_run_id) &&
        row.tool_calls?.some(call => call.id === source.parent_tool_call_id));
      const call = parent?.tool_calls?.find(item => item.id === source.parent_tool_call_id);
      const old = rows.get(source.agent_path);
      rows.set(source.agent_path, { agent_id: source.agent_id, agent_path: source.agent_path,
        agent: source.agent_path.split('/')[1], task: old?.task ?? String(call?.args.task ?? ''),
        tool_call_id: source.parent_tool_call_id, parent_batch_id: agent.parent_batch_id, status: agent.status });
    }
    return [...rows.values()];
  }, [saved, sessionId, currentRun]);
  const stored = detail?.sid === sessionId && detail.value.agent_id === selected ? detail.value : undefined;
  const record = stored ?? records.find(row => row.agent_id === selected);
  const live = record ? currentRun?.agents?.[record.agent_path] : undefined;
  useEffect(() => {
    if (selected && live?.messages) history.merge(live.messages.filter(row => !row.content_unavailable));
  }, [selected, live?.messages, history.merge]);
  const messages = selected ? history.messages : [];
  const toolStates = { ...stored?.tool_states };
  for (const [id, phase] of Object.entries(live?.tool_states ?? {})) {
    const old = toolStates[id];
    toolStates[id] = { ...(old?.batch_id === phase.batch_id ? old : {}), ...phase };
  }
  return { records, selected, record, stored, live, messages, toolStates, loading, loadingDetail, listError, detailError,
    history: { ...history.pagination, loadOlder: history.loadOlder },
    select: (id?: string) => { history.reset(); setDetailError(''); setSelection(id ? { sid: sessionId, id } : undefined); },
    retry: () => setRevision(value => value + 1) };
}
