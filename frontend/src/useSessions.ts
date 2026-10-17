import { useCallback, useRef, useState } from 'react';
import { errorText } from './api';
import type { WebApi } from './api';
import type { SessionRow } from './types';

export function useSessions(api: WebApi, notice: (text: string) => void) {
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [managingSessions, setManaging] = useState(false);
  const version = useRef(0);
  const inFlight = useRef(false);
  const refreshSessions = useCallback(async () => {
    const generation = ++version.current;
    const rows = await api.sessions();
    if (generation === version.current) setSessions(rows);
  }, [api]);

  async function change(perform: () => Promise<void>) {
    if (inFlight.current) return false;
    inFlight.current = true; setManaging(true); version.current++; notice('');
    try {
      await perform();
      version.current++;
      try { await refreshSessions(); }
      catch { notice('操作已保存，会话列表刷新未确认，请刷新页面核对。'); }
      return true;
    } catch (error) {
      notice(errorText(error));
      try { await refreshSessions(); }
      catch { notice(errorText(error) + ' 会话列表刷新未确认。'); }
      return false;
    } finally { inFlight.current = false; setManaging(false); }
  }

  return { sessions, managingSessions, refreshSessions,
    updateSession: (id: string, update: { title?: string; pinned?: boolean }) => change(async () => {
      const saved = await api.updateSession(id, update);
      setSessions(rows => rows.map(row => row.session_id === id ? saved : row)
        .sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned)));
    }),
    reorderSessions: (ids: string[]) => change(async () => { setSessions(await api.reorderSessions(ids)); }),
    deleteSession: (id: string) => change(async () => {
      await api.deleteSession(id);
      setSessions(rows => rows.filter(row => row.session_id !== id));
    }),
  };
}
