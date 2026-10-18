import { useEffect, useState } from 'react';
import { errorText, WebApi } from './api';
import { useMessageHistory } from './useMessageHistory';
import type { SubagentList } from './useSubagentList';
import type { RunView, SubagentDetail } from './types';

export function useSubagents(api: WebApi, sessionId: string, list: SubagentList, run?: RunView | null, initialAgentId?: string) {
  const history = useMessageHistory(api);
  const [selection, setSelection] = useState<{ sid: string; id: string }>();
  const [detail, setDetail] = useState<{ sid: string; value: SubagentDetail }>();
  const [detailError, setDetailError] = useState('');
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

  const stored = detail?.sid === sessionId && detail.value.agent_id === selected ? detail.value : undefined;
  const record = stored ?? list.records.find(row => row.agent_id === selected);
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
  return { records: list.records, selected, record, stored, live, messages, toolStates, loading: list.loading, loadingDetail, listError: list.error, detailError,
    history: { ...history.pagination, loadOlder: history.loadOlder },
    select: (id?: string) => { history.reset(); setDetailError(''); setSelection(id ? { sid: sessionId, id } : undefined); },
    retry: () => selected ? setRevision(value => value + 1) : list.retry() };
}
