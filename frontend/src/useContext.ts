import { useEffect, useState } from 'react';
import type { WebApi } from './api';
import type { RunView, SessionView } from './types';
import type { ContextView } from './contextTypes';

export function useContextView(api: WebApi, session: SessionView | null, run: RunView | null) {
  const [stored, setStored] = useState<{ key: string; value: ContextView }>();
  const [failure, setFailure] = useState<{ key: string; text: string }>();
  const [pending, setPending] = useState<string>();
  const [revision, setRevision] = useState(0);
  const sid = session?.session_id;
  const tools = JSON.stringify(session?.active_tools);
  const snapshot = run?.session_id === sid ? run?.context_snapshot : undefined;
  const key = JSON.stringify([sid, session?.model_id, tools, run?.run_id, run?.status, run?.done,
    snapshot, session?.busy, revision]);
  useEffect(() => {
    if (!sid) return;
    let current = true;
    setPending(key);
    setFailure(undefined);
    async function read() {
      try {
        const value = await api.context(sid!);
        if (value.session_id !== sid) throw new Error('context_session_mismatch');
        if (current) setStored({ key, value });
      } catch {
        if (current) setFailure({ key, text: '上下文读取失败，可以重试；当前任务不会受到影响。' });
      } finally { if (current) setPending(undefined); }
    }
    void read();
    return () => { current = false; };
  }, [api, sid, key]);
  return { value: stored?.key === key ? stored.value : undefined,
    error: failure?.key === key ? failure.text : '',
    loading: !!sid && (pending === key || stored?.key !== key && failure?.key !== key),
    retry: () => setRevision(value => value + 1) };
}
