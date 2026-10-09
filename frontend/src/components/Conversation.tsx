import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import type { MessageRow, ToolCallRow, ToolState } from '../types';
import ToolCall from './ToolCall';
import styles from '../App.module.css';

function safeUrl(url: string) {
  return /^(https?:\/\/|\/[^/]|#)/i.test(url) ? url : '';
}

export default function Conversation({ messages, compacted, toolStates = {} }: {
  messages: MessageRow[]; compacted: boolean; toolStates?: Record<string, ToolState>;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const [away, setAway] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const results = new Map<ToolCallRow, MessageRow>();
  const matchedResults = new Set<string>();
  let owner: MessageRow | undefined;
  for (const row of messages) {
    if (row.type === 'ai') owner = row;
    else if (row.type === 'tool') {
      const call = owner?.tool_calls?.find(item => item.id === row.tool_call_id);
      if (call) { results.set(call, row); matchedResults.add(row.id); }
    }
  }
  useEffect(() => {
    if (nearBottom.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [messages]);

  async function copy(row: MessageRow) {
    try { await navigator.clipboard.writeText(row.content); setCopied(row.id); }
    catch { setCopied('failed'); }
  }

  return <div ref={scroll} className={styles.conversation} onScroll={() => {
    const element = scroll.current!;
    nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
    setAway(!nearBottom.current);
  }}>
    {!messages.length ? <div className={styles.welcome}>
      <div className={styles.welcomeMark}>N</div>
      <h1>今天想完成什么？</h1><p>描述你的任务，我们从这里开始。</p>
    </div> : <div className={styles.messageList}>
      {compacted && <p className={styles.compactionNote}>较早内容已压缩，恢复记录包含当前可用上下文。</p>}
      {messages.filter(row => !matchedResults.has(row.id)).map(row => <article key={row.id} className={`${styles.message} ${row.type === 'human' && !row.is_context_summary ? styles.userMessage : styles.assistantMessage}`}>
        {row.is_context_summary ? <details className={styles.summary}><summary>较早会话摘要</summary><pre>{row.content}</pre></details> : <>
          {row.type === 'ai' && <hr className={styles.replyDivider} />}
          {(row.type === 'tool' || row.provisional) && <div className={styles.speaker}>{row.type === 'tool' && '工具结果'}{row.provisional && <span>{row.state === 'unconfirmed' ? '未完成内容' : '正在生成'}</span>}</div>}
          <div className={styles.messageBody}>
            {row.provisional || row.type === 'tool' ? <div className={styles.draftText}>{row.content}</div> : <ReactMarkdown skipHtml urlTransform={safeUrl}
              components={{ img: ({ alt }) => <span>{alt ? `[图片：${alt}]` : '[图片]'}</span>,
                a: ({ children, href }) => <a href={href} target="_blank" rel="noreferrer noopener">{children}</a> }}>
              {row.content}
            </ReactMarkdown>}
          </div>
          {row.tool_calls?.map(call => {
            const result = results.get(call);
            const phase = toolStates[call.id];
            const correlated = phase && (phase.run_id === row.run_id || phase.run_id === result?.run_id ||
              (!result && phase.state === 'running'));
            return <ToolCall key={call.id} call={call} result={result} phase={correlated ? phase : undefined} />;
          })}
          {row.type === 'ai' && row.content && <button className={styles.copyButton} onClick={() => void copy(row)}
            aria-label="复制回答">{copied === row.id ? '已复制' : copied === 'failed' ? '复制未成功' : '复制'}</button>}
        </>}
      </article>)}
    </div>}
    {away && <button className={styles.latestButton} onClick={() => {
      nearBottom.current = true; setAway(false); if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
    }}>回到最新 ↓</button>}
  </div>;
}
