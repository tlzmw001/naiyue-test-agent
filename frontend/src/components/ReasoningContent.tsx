import { useEffect, useRef, useState } from 'react';
import type { MessageRow, ReasoningLoader } from '../types';
import styles from '../App.module.css';

export default function ReasoningContent({ row, loadReasoning }: { row: MessageRow; loadReasoning?: ReasoningLoader }) {
  const [extra, setExtra] = useState(''), [complete, setComplete] = useState(false);
  const [pending, setPending] = useState(false), [error, setError] = useState('');
  const generation = useRef(0);
  useEffect(() => { generation.current++; setExtra(''); setComplete(false); return () => { generation.current++; }; }, [row.id, row.revision]);
  if (!row.reasoning) return null;
  async function load() {
    if (!loadReasoning || pending) return;
    const version = generation.current;
    setPending(true); setError('');
    try {
      const result = await loadReasoning(row, (row.reasoning ?? '').length + extra.length);
      if (version === generation.current) { setExtra(value => value + result.text); setComplete(result.complete); }
    } catch { if (version === generation.current) setError('读取思考内容失败，可以重试。'); }
    finally { if (version === generation.current) setPending(false); }
  }
  return <details className={styles.summary}><summary>思考内容{row.provisional || row.state === 'unconfirmed' ? ' · 未完成' : ''}</summary>
    <pre>{row.reasoning}{extra}</pre>
    {row.reasoning_truncated && !complete && <><p>当前为思考内容预览。</p>
      {loadReasoning && row.detail && <button className={styles.copyButton} disabled={pending} onClick={() => void load()}>加载更多思考</button>}</>}
    {error && <p role="alert">{error}</p>}
  </details>;
}
