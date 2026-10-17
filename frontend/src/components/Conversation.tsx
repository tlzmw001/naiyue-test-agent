import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import type { MessageRow, SessionRecovery, ToolCallRow, ToolState } from '../types';
import ToolCall from './ToolCall';
import styles from '../App.module.css';

function safeUrl(url: string) {
  return /^(https?:\/\/|\/[^/]|#)/i.test(url) ? url : '';
}

export default function Conversation({ messages, compacted, toolStates = {}, stoppingRunId, recovery, recovering = false, onRecover }: {
  messages: MessageRow[]; compacted: boolean; toolStates?: Record<string, ToolState>; stoppingRunId?: string; recovery?: SessionRecovery;
  recovering?: boolean; onRecover?: (action: 'continue' | 'restore_approval') => void;
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
  }, [messages, toolStates]);

  async function copy(row: MessageRow) {
    try { await navigator.clipboard.writeText(row.content); setCopied(row.id); }
    catch { setCopied('failed'); }
  }

  return <div ref={scroll} className={styles.conversation} onScroll={() => {
    const element = scroll.current!;
    nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
    setAway(!nearBottom.current);
  }}>
    {!messages.length && !recovery ? <div className={styles.welcome}>
      <div className={styles.welcomeMark}>N</div>
      <h1>今天想完成什么？</h1><p>描述你的任务，我们从这里开始。</p>
    </div> : <div className={styles.messageList}>
      {recovery && <aside className={styles.recoveryNotice} aria-label="历史状态">
        <strong>{recovering ? '正在恢复会话' : '只读历史'}</strong>
        <p>{recovery.kind === 'pending_approval' ? '上次运行保存在工具审批前，当前尚未恢复审批。' :
          recovery.kind === 'unavailable' ? '当前无法安全恢复此会话。下方仅展示能够读取的已保存消息。' :
          '上次运行未正常结束。下方是已保存消息，旧调用没有自动重跑。'}</p>
        <span>原因：{recovery.reason}</span>
        {onRecover && <div className={styles.recoveryActions}>
          {recovery.actions.includes('restore_approval') && <button onClick={() => onRecover('restore_approval')}
            disabled={recovering}>{recovering ? '正在恢复待审批…' : '重试恢复待审批'}</button>}
          {recovery.actions.includes('continue') && <>
          <p>继续会结束旧任务，并补充未确认的结果说明。发送新消息后才会启动运行。</p>
          <button aria-label="结束旧运行并继续会话" aria-busy={recovering}
            onClick={() => onRecover('continue')} disabled={recovering}>{recovering ? '正在保存恢复状态…' : '结束旧运行并继续会话'}</button>
          </>}
        </div>}
      </aside>}
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
            const correlated = phase && (phase.batch_id ? phase.batch_id === row.id :
              phase.run_id === row.run_id || phase.run_id === result?.run_id || (!result && phase.state === 'running'));
            return <ToolCall key={call.id} call={call} result={result} phase={correlated ? phase : undefined}
              unconfirmed={!result && recovery?.unconfirmed_tool_call_ids.includes(call.id)}
              stopping={correlated && phase.run_id === stoppingRunId} />;
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
