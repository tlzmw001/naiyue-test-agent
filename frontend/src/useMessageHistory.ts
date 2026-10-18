import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, errorText, WebApi } from './api';
import { mergeMessages } from './events';
import type { HistoryPage, MessageRow } from './types';

type Snapshot = { messages: MessageRow[] } & Partial<Omit<HistoryPage, 'messages'>>;
type State = { messages: MessageRow[]; page?: HistoryPage; loading: boolean; error: string; stale: boolean };
const empty = (): State => ({ messages: [], loading: false, error: '', stale: false });

export function useMessageHistory(api: WebApi) {
  const [state, setState] = useState<State>(empty);
  const value = useRef(state);
  const scope = useRef<{ sessionId: string; agentId?: string } | undefined>(undefined);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const pending = useRef<Promise<void> | null>(null);
  const retrySnapshot = useRef<Snapshot | null>(null);
  const update = useCallback((next: State) => { value.current = next; setState(next); }, []);
  const cancel = useCallback(() => {
    generation.current++;
    controller.current?.abort(); controller.current = null; pending.current = null;
    retrySnapshot.current = null;
    update({ ...value.current, loading: false });
  }, [update]);
  useEffect(() => () => { generation.current++; controller.current?.abort(); }, []);

  const open = useCallback((sessionId: string, agentId: string | undefined, snapshot: Snapshot) => {
    const arriving = scope.current ? [] : value.current.messages;
    cancel(); scope.current = { sessionId, agentId };
    update({ ...empty(), messages: mergeMessages(snapshot.messages, arriving),
      page: snapshot.coverage ? snapshot as HistoryPage : undefined });
  }, [cancel, update]);
  const reset = useCallback(() => { cancel(); scope.current = undefined; update(empty()); }, [cancel, update]);
  const merge = useCallback((rows: MessageRow[]) => {
    update({ ...value.current, messages: mergeMessages(value.current.messages, rows) });
  }, [update]);

  const perform = useCallback((work: (signal: AbortSignal, valid: () => boolean) => Promise<void>) => {
    const version = generation.current, request = new AbortController();
    controller.current = request;
    const valid = () => generation.current === version && controller.current === request && !request.signal.aborted;
    update({ ...value.current, loading: true, error: '' });
    const task = (async () => {
      try { await work(request.signal, valid); }
      catch (error) {
        if (valid()) update({ ...value.current, error: errorText(error),
          stale: error instanceof ApiError && error.code === 'history_cursor_stale' });
      } finally {
        if (valid()) { controller.current = null; pending.current = null; update({ ...value.current, loading: false }); }
      }
    })();
    pending.current = task;
    return task;
  }, [update]);

  const synchronizePage = useCallback(async (snapshot: Snapshot, signal: AbortSignal, valid: () => boolean) => {
    if (!scope.current || !valid()) return;
    const { sessionId, agentId } = scope.current, baseline = value.current;
    if (!snapshot.coverage) { if (valid()) merge(snapshot.messages); return; }
    const first = snapshot as HistoryPage;
    retrySnapshot.current = snapshot;
    let current = first, collected = first.messages;
    const changed = baseline.page && baseline.page.coverage.history_version !== first.coverage.history_version;
    const sequences = baseline.messages.flatMap(row => row.sequence == null ? [] : [row.sequence]);
    const target = sequences.length ? (changed ? Math.min(...sequences) : Math.max(...sequences) + 1) : undefined;
    const savedIds = new Set(baseline.messages.filter(row => !row.provisional).map(row => row.id));
    const cursors = new Set<string>();
    while (current.has_more && current.next_cursor && valid()) {
      const minimum = Math.min(...collected.flatMap(row => row.sequence == null ? [] : [row.sequence]));
      if (target != null ? minimum <= target : !savedIds.size || collected.some(row => savedIds.has(row.id))) break;
      if (cursors.has(current.next_cursor)) throw new ApiError('invalid_history_cursor');
      cursors.add(current.next_cursor);
      const older = await api.messages(sessionId, current.next_cursor, agentId, signal);
      if (!valid()) return;
      if (older.coverage.history_version !== first.coverage.history_version || older.coverage.upper !== first.coverage.upper)
        throw new ApiError('history_cursor_stale');
      if (older.has_more && !older.messages.length) throw new ApiError('invalid_history_cursor');
      collected = mergeMessages(collected, older.messages, true); current = older;
    }
    if (!valid()) return;
    update({ ...value.current, messages: mergeMessages(value.current.messages, collected, !baseline.messages.length),
      page: baseline.page && !changed && baseline.messages.length ? baseline.page : current,
      error: '', stale: false });
    retrySnapshot.current = null;
  }, [api, merge, update]);

  const synchronize = useCallback(async (snapshot: Snapshot) => {
    const version = generation.current;
    while (pending.current) {
      await pending.current;
      if (generation.current !== version) return;
    }
    if (generation.current !== version || !scope.current) return;
    await perform((signal, valid) => synchronizePage(snapshot, signal, valid));
  }, [perform, synchronizePage]);

  const loadOlder = useCallback(async () => {
    const current = value.current, selected = scope.current;
    if (!selected || current.loading || (!retrySnapshot.current && !current.stale &&
        (!current.page?.has_more || !current.page.next_cursor))) return;
    await perform(async (signal, valid) => {
      if (current.stale) {
        const latest = await api.messages(selected.sessionId, undefined, selected.agentId, signal);
        return synchronizePage(latest, signal, valid);
      }
      if (retrySnapshot.current) return synchronizePage(retrySnapshot.current, signal, valid);
      const older = await api.messages(selected.sessionId, current.page!.next_cursor!, selected.agentId, signal);
      if (!valid()) return;
      if (older.coverage.history_version !== current.page!.coverage.history_version ||
          older.coverage.upper !== current.page!.coverage.upper) throw new ApiError('history_cursor_stale');
      if (older.has_more && (!older.messages.length || older.next_cursor === current.page!.next_cursor))
        throw new ApiError('invalid_history_cursor');
      update({ ...value.current, messages: mergeMessages(value.current.messages, older.messages, true),
        page: older, error: '', stale: false });
    });
  }, [api, perform, synchronizePage, update]);

  return { messages: state.messages, get sessionId() { return scope.current?.sessionId; }, open, reset, cancel, merge, synchronize, loadOlder,
    pagination: { enabled: !!state.page, hasMore: state.page?.has_more ?? false,
      loading: state.loading, error: state.error } };
}
