import { useEffect, useMemo, useRef, useState } from 'react';
import { errorText, type BrowserConnection } from './api';
import { LegacyReader } from './legacyReader';
import Conversation from './components/Conversation';
import type { MessageRow, SessionRow } from './types';
import styles from './App.module.css';

/** An unavailable old identity can be read without registering or executing it. */
export default function LegacyHistory({ connection }: { connection: BrowserConnection }) {
  const reader = useMemo(() => new LegacyReader(connection), [connection]);
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [selected, setSelected] = useState('');
  const [root, setRoot] = useState('');
  const [reason, setReason] = useState('');
  const [messages, setMessages] = useState<MessageRow[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [listing, setListing] = useState(true);
  const [before, setBefore] = useState(0);
  const checkpoint = useRef('');
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    reader.sessions(controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setRows(value.sessions); setRoot(value.root); setReason(value.execution_root_error.reason);
      setSelected(value.sessions[0]?.session_id ?? '');
    }).catch(e => { if (!controller.signal.aborted) setError(errorText(e)); })
      .finally(() => { if (!controller.signal.aborted) setListing(false); });
    return () => { controller.abort(); active.current?.abort(); };
  }, [reader]);
  async function load(id: string, start: number, signal: AbortSignal, older: boolean) {
    const from = Math.max(0, start - 25), page: MessageRow[] = [];
    for (let i = from; i < start; i++) page.push(await reader.message(id, i, checkpoint.current, signal));
    if (!signal.aborted) { setMessages(previous => older ? [...page, ...previous] : page); setBefore(from); }
  }
  useEffect(() => {
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    setMessages([]); setBefore(0); setError('');
    if (!selected) return () => controller.abort();
    setLoading(true);
    reader.history(selected, controller.signal).then(async value => {
      if (controller.signal.aborted) return;
      if (!Number.isSafeInteger(value.message_count) || value.message_count < 0) throw new Error('history_content_invalid');
      checkpoint.current = value.checkpoint_id;
      await load(selected, value.message_count, controller.signal, false);
    }).catch(e => { if (!controller.signal.aborted) setError(errorText(e)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [selected, reader]);
  async function older() {
    if (!active.current || loading) return;
    const signal = active.current.signal;
    setLoading(true); setError('');
    try { await load(selected, before, signal, true); }
    catch (e) { if (!signal.aborted) setError(errorText(e)); }
    finally { if (!signal.aborted) setLoading(false); }
  }
  return <div className={styles.app}><main className={styles.workspace}>
    <header className={styles.header}><strong>历史档案 · 只读</strong>
      <label>会话 <select aria-label="选择历史会话" value={selected} onChange={e => setSelected(e.target.value)}>
        {rows.map(row => <option key={row.session_id} value={row.session_id}>{row.title || row.session_id}</option>)}
      </select></label>
    </header>
    {root && <p className={styles.sidebarEmpty}>执行目录不可用（{reason}）。{root}。此档案不能发起运行。</p>}
    {error && <p role="alert" className={styles.sidebarEmpty}>{error}</p>}
    {(listing || loading) && <p role="status" className={styles.sidebarEmpty}>正在读取历史…</p>}
    {!listing && !rows.length && !error && <p className={styles.sidebarEmpty}>暂无历史会话</p>}
    {!listing && selected && !loading && !error && !messages.length && <p className={styles.sidebarEmpty}>暂无历史消息</p>}
    {!!messages.length && <Conversation messages={messages} compacted={false} contentScope={selected}
      history={{ enabled: true, hasMore: before > 0, loading, error, loadOlder: older }} />}
  </main></div>;
}
