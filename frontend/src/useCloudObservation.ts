import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from './cloudApi';
import type { CloudArchive, CloudMessage, CloudSnapshot, CloudThread, CloudUsage } from './cloudTypes';
import { cloudError } from './cloudPresentation';
import { runReconnectDelays } from './uiSettings';

export function useCloudObservation(api: CloudApi, sid: string, bid: string, taskId: string, active: boolean, thread?: string) {
  const [snapshot, setSnapshot] = useState<CloudSnapshot>();
  const [archive, setArchive] = useState<CloudArchive>();
  const [threads, setThreads] = useState<CloudThread[]>([]);
  const [usage, setUsage] = useState<CloudUsage>();
  const [error, setError] = useState(''); const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false); const [revision, setRevision] = useState(0);
  const [threadCursor, setThreadCursor] = useState<string | null>(null); const [threadsLoading, setThreadsLoading] = useState(false);
  const threadPage = useRef<AbortController | undefined>(undefined);
  const page = useRef<AbortController | undefined>(undefined);
  useEffect(() => {
    setSnapshot(undefined); setArchive(undefined); setThreads([]); setUsage(undefined); setThreadCursor(null); setError(''); setLoading(true);
    return () => { page.current?.abort(); threadPage.current?.abort(); };
  }, [api, sid, bid, thread]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    let epoch = ''; let sequence = 0; let cursor: string | undefined;
    const valid = (value: CloudSnapshot) => {
      if (controller.signal.aborted) return;
      if (value.task.task_id !== taskId) throw new Error('cloud_task_identity_mismatch');
      epoch = value.live.epoch; sequence = value.live.seq; cursor = value.cursor;
      setSnapshot(value);
      setArchive(old => {
        const earliest = value.archive.messages[0]?.sequence ?? 0;
        const older = old?.epoch === value.archive.epoch ? old.messages.filter(row => (row.sequence ?? 0) < earliest) : [];
        const ids = new Set(value.archive.messages.map(row => row.id));
        return { ...value.archive, next_cursor: older.length ? old!.next_cursor : value.archive.next_cursor,
          messages: [...older.filter(row => !ids.has(row.id)), ...value.archive.messages] };
      });
      setError(''); setLoading(false);
      void api.usage(sid, bid, controller.signal).then(value => { if (!controller.signal.aborted) setUsage(value); })
        .catch(() => { if (!controller.signal.aborted) { console.warn('cloud_usage_read_failed'); setError('用量读取失败，可重新连接后核对。'); } });
      void api.threads(sid, bid, undefined, controller.signal).then(value => {
        if (!controller.signal.aborted) {
          setThreads(old => [...new Map([...old, ...value.threads].map(row => [row.thread_id, row])).values()]);
          setThreadCursor(value.next_cursor);
        }
      }).catch(() => { if (!controller.signal.aborted) { console.warn('cloud_threads_read_failed'); setError('子线程目录读取失败，可重新连接。'); } });
    };
    async function connect(attempt = 0) {
      try {
        valid(await api.snapshot(sid, bid, thread, controller.signal));
        if (controller.signal.aborted) return;
        await api.observe(sid, bid, thread, cursor, controller.signal, frame => {
          if (controller.signal.aborted) return;
          if (frame.event === 'snapshot') valid(frame.data as CloudSnapshot);
          else if (frame.event === 'update') {
            const live = frame.data as CloudSnapshot['live'];
            if (live.epoch !== epoch || live.seq > sequence + 1) throw new Error('cloud_observation_gap');
            if (live.seq <= sequence) return;
            sequence = live.seq; cursor = frame.id;
            setSnapshot(old => old ? { ...old, live } : old);
          } else if (frame.event === 'task_deleting') { setError('任务正在删除，观察已结束'); controller.abort(); }
          else if (frame.event === 'unavailable') throw new Error('cloud_observation_unavailable');
        });
        if (!controller.signal.aborted) throw new Error('cloud_observation_closed');
      } catch (failure) {
        if (controller.signal.aborted) return;
        console.warn('cloud_observation_failed'); setError('观察连接中断，已保存内容仍可查看。' + cloudError(failure)); setLoading(false);
        if (attempt < runReconnectDelays.length) timer = setTimeout(() => void connect(attempt + 1), runReconnectDelays[attempt]);
      }
    }
    void connect();
    return () => { controller.abort(); page.current?.abort(); threadPage.current?.abort(); clearTimeout(timer); };
  }, [api, sid, bid, taskId, active, thread, revision]);
  async function loadOlder() {
    if (!archive?.next_cursor || page.current || !active) return;
    const controller = new AbortController(); page.current = controller; setMore(true);
    try {
      const older = await api.history(sid, bid, thread, archive.next_cursor, controller.signal);
      if (!controller.signal.aborted) setArchive(old => {
        if (!old || old.epoch !== older.epoch) return old;
        const ids = new Set(old.messages.map(row => row.id));
        return { ...old, next_cursor: older.next_cursor, messages: [...older.messages.filter(row => !ids.has(row.id)), ...old.messages] };
      });
    } catch (failure) { if (!controller.signal.aborted) setError(cloudError(failure)); }
    finally { if (page.current === controller) { page.current = undefined; setMore(false); } }
  }
  async function loadThreads() {
    if (!threadCursor || threadPage.current || !active) return;
    const controller = new AbortController(); threadPage.current = controller; setThreadsLoading(true);
    try {
      const value = await api.threads(sid, bid, threadCursor, controller.signal);
      if (!controller.signal.aborted) {
        setThreads(old => [...new Map([...old, ...value.threads].map(row => [row.thread_id, row])).values()]);
        setThreadCursor(value.next_cursor);
      }
    } catch (failure) { if (!controller.signal.aborted) { console.warn('cloud_threads_read_failed'); setError(cloudError(failure)); } }
    finally { if (threadPage.current === controller) { threadPage.current = undefined; setThreadsLoading(false); } }
  }
  const merged = new Map<string, CloudMessage>((archive?.messages ?? []).map(row => [row.id, row]));
  for (const row of snapshot?.live.messages ?? []) merged.set(row.id, row);
  async function content(row: CloudMessage, field: string, signal?: AbortSignal, offset = 0) {
    if (!row?.detail) throw new Error('cloud_message_not_saved');
    let text = ''; let next: number | null = offset; let digest: string | undefined;
    do {
      signal?.throwIfAborted();
      const value = await api.detail(sid, bid, row.detail, field, next, signal);
      signal?.throwIfAborted();
      if (value.message_id !== row.detail.message_id || value.thread_id !== row.detail.thread_id || value.revision !== row.detail.revision || value.offset !== next || value.field !== field || digest && value.body_sha256 !== digest) throw new Error('cloud_detail_identity_mismatch');
      digest = value.body_sha256;
      text += value.text;
      if (text.length > 2 * 1024 * 1024 || value.next_offset !== null && value.next_offset <= next) throw new Error('cloud_detail_budget');
      next = value.next_offset;
    } while (next !== null);
    return text;
  }
  const directory = new Map(threads.map(row => [row.thread_id, row]));
  for (const row of snapshot?.live.threads ?? []) {
    if (!directory.has(row.thread_id))
      directory.set(row.thread_id, { ...row, role: row.source ? 'child' : 'root', agent: row.source?.agent_id });
  }
  return { snapshot, rows: [...merged.values()], threads: [...directory.values()], usage, error, loading, loadOlder, more,
    hasMore: !!archive?.next_cursor, hasMoreThreads: !!threadCursor, loadThreads, threadsLoading,
    reconnect: () => setRevision(v => v + 1), content };
}
