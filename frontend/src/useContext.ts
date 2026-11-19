import { useEffect, useMemo, useState } from 'react';
import type { WebApi } from './api';
import type { RunView, SessionView } from './types';
import type { ContextView } from './contextTypes';

export function useContextView(api: WebApi, session: SessionView | null, run: RunView | null, active = true) {
  const [stored, setStored] = useState<{ api: WebApi; identity: string; key: string; value: ContextView }>();
  const [failure, setFailure] = useState<{ key: string; text: string }>();
  const [pending, setPending] = useState<string>();
  const [revision, setRevision] = useState(0);
  const sid = session?.session_id;
  const tools = JSON.stringify(session?.active_tools);
  const identity = JSON.stringify([sid, session?.model_id, session?.thinking_level, tools]);
  const snapshot = run?.session_id === sid ? run?.context_snapshot : undefined;
  const key = JSON.stringify([sid, session?.model_id, session?.thinking_level, tools, run?.run_id, run?.status, run?.done,
    snapshot, session?.busy, revision]);
  // A retained stream snapshot still belongs to the configuration where it first arrived.
  const observed = useMemo(() => ({ api, identity, snapshot }), [api, snapshot]);
  const hasLive = run?.status === 'running' && run.session_id === sid && snapshot?.run_id === run.run_id &&
    snapshot?.model_id === session?.model_id && observed.identity === identity;
  useEffect(() => {
    if (!sid || !active) return;
    let current = true;
    if (hasLive && snapshot) setStored({ api, identity, key, value: { session_id: sid,
      current: { basis: 'last_sent', model_id: snapshot.model_id, estimate: snapshot.estimate }, latest_request: snapshot } });
    setPending(key);
    setFailure(undefined);
    async function read() {
      try {
        const value = await api.context(sid!);
        if (value.session_id !== sid) throw new Error('context_session_mismatch');
        if (current) setStored(previous => {
          const cached = previous?.api === api && previous.identity === identity ? previous.value.current.estimate : null;
          const old = cached;
          return { api, identity, key, value: value.current.estimate || !old ? value : {
            ...value, current: { basis: 'unavailable', model_id: session?.model_id, estimate: old } } };
        });
      } catch {
        if (current) setFailure({ key, text: '上下文读取失败，可以重试；当前任务不会受到影响。' });
      } finally { if (current) setPending(undefined); }
    }
    void read();
    return () => { current = false; };
  }, [api, sid, key, active]);
  const previous = stored?.api === api && stored.identity === identity ? stored : undefined;
  let value = previous ? { ...previous.value, current: previous.key === key ? previous.value.current :
    { ...previous.value.current, basis: 'unavailable' as const } } : undefined;
  const live = snapshot;
  if (hasLive && live) {
    value = { session_id: sid!, current: { basis: 'last_sent', model_id: live.model_id, estimate: live.estimate }, latest_request: live };
  }
  return { value,
    error: failure?.key === key ? failure.text : '',
    loading: !hasLive && !!sid && (pending === key || stored?.key !== key && failure?.key !== key),
    retry: () => setRevision(value => value + 1) };
}
