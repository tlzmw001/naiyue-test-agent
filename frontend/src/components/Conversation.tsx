import { useLayoutEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import type { AgentView, HistoryControls, MessageRow, SessionRecovery, ToolCallRow, ToolState } from '../types';
import ToolCall from './ToolCall';
import styles from '../App.module.css';

function safeUrl(url: string) {
  return /^(https?:\/\/|\/[^/]|#)/i.test(url) ? url : '';
}

export default function Conversation({ messages, compacted, toolStates = {}, agents = {}, stopAgent, openAgent, stoppingRunId, recovery, recovering = false, onRecover, history }: {
  messages: MessageRow[]; compacted: boolean; toolStates?: Record<string, ToolState>; stoppingRunId?: string; recovery?: SessionRecovery;
  recovering?: boolean; onRecover?: (action: 'continue' | 'restore_approval') => void;
  agents?: Record<string, AgentView>;
  stopAgent?: (agentPath: string, originRunId: string) => void;
  openAgent?: (agentPath: string) => void;
  history?: HistoryControls;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const anchor = useRef<{ rows: { id: string; offset: number }[]; height: number; top: number } | null>(null);
  const loadingPage = useRef(false);
  const standaloneResults = useRef(new Set<string>());
  const [away, setAway] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const results = new Map<ToolCallRow, MessageRow>();
  const matchedResults = new Set<string>();
  const byId = new Map(messages.map(row => [row.id, row]));
  let owner: MessageRow | undefined;
  for (const row of messages) {
    if (row.type === 'ai') owner = row;
    else if (row.type === 'tool') {
      const parent = row.parent_message_id !== undefined ? byId.get(row.parent_message_id ?? '') : owner;
      const call = parent?.tool_calls?.find(item => item.id === row.tool_call_id);
      if (call) { results.set(call, row); matchedResults.add(row.id); }
    }
  }
  function rememberPosition() {
    const element = scroll.current;
    if (!element) return;
    const bounds = element.getBoundingClientRect();
    const rows = Array.from(element.querySelectorAll<HTMLElement>('[data-message-id]'))
      .filter(node => node.getBoundingClientRect().bottom > bounds.top && node.getBoundingClientRect().top < bounds.bottom)
      .map(node => ({ id: node.dataset.messageId!, offset: node.getBoundingClientRect().top - bounds.top }));
    anchor.current = { rows, height: element.scrollHeight, top: element.scrollTop };
  }
  useLayoutEffect(() => {
    const element = scroll.current;
    if (!element) return;
    if (nearBottom.current) element.scrollTop = element.scrollHeight;
    else if (anchor.current) {
      const nodes = Array.from(element.querySelectorAll<HTMLElement>('[data-message-id]'));
      const saved = anchor.current.rows.find(row => nodes.some(node => node.dataset.messageId === row.id));
      const node = saved && nodes.find(node => node.dataset.messageId === saved.id);
      if (node && saved) element.scrollTop += node.getBoundingClientRect().top - element.getBoundingClientRect().top - saved.offset;
      else if (loadingPage.current) element.scrollTop = anchor.current.top + element.scrollHeight - anchor.current.height;
    }
    rememberPosition();
    standaloneResults.current = new Set(messages.filter(row => row.type === 'tool' && !matchedResults.has(row.id)).map(row => row.id));
  }, [messages, toolStates, agents, history?.loading, history?.error]);

  async function loadOlder() {
    if (!history || history.loading || history.disabled || loadingPage.current) return;
    rememberPosition();
    nearBottom.current = false;
    loadingPage.current = true;
    try { await history.loadOlder(); }
    finally { loadingPage.current = false; }
  }

  async function copy(row: MessageRow) {
    try { await navigator.clipboard.writeText(row.content); setCopied(row.id); }
    catch { setCopied('failed'); }
  }

  return <div ref={scroll} className={styles.conversation} onScroll={() => {
    const element = scroll.current!;
    nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
    setAway(!nearBottom.current);
    rememberPosition();
    if (element.scrollTop <= 96 && history?.hasMore && !history.error) void loadOlder();
  }}>
    {!messages.length && !recovery ? <div className={styles.welcome}>
      <div className={styles.welcomeMark}>N</div>
      <h1>今天想完成什么？</h1><p>描述你的任务，我们从这里开始。</p>
    </div> : <div className={styles.messageList}>
      {history?.enabled && <div className={styles.historyStatus} aria-live="polite">
        {history.error ? <><span role="alert">{history.error}</span><button disabled={history.loading || history.disabled}
          onClick={() => void loadOlder()}>重试加载历史</button></> : history.loading ? <span role="status">正在读取历史…</span> :
          history.hasMore ? <button disabled={history.disabled} onClick={() => void loadOlder()}>加载更早消息</button> :
            <span>已到达历史起点</span>}
      </div>}
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
      {compacted && <p className={styles.compactionNote}>较早上下文已压缩，原始聊天记录仍可查看。</p>}
      {messages.filter(row => !matchedResults.has(row.id)).map(row => <article key={row.id} data-message-id={row.type === 'tool' ? undefined : row.id} className={`${styles.message} ${row.type === 'human' && !row.is_context_summary ? styles.userMessage : styles.assistantMessage}`}>
        {row.is_context_summary ? <details className={styles.summary}><summary>较早会话摘要</summary><pre>{row.content}</pre></details> : <>
          {row.type === 'ai' && <hr className={styles.replyDivider} />}
          {(row.type === 'tool' || row.provisional) && <div className={styles.speaker}>{row.type === 'tool' && '工具结果'}{row.provisional && <span>{row.state === 'unconfirmed' ? '未完成内容' : '正在生成'}</span>}</div>}
          <div className={styles.messageBody} data-message-id={row.type === 'tool' ? row.id : undefined}>
            {row.provisional || row.type === 'tool' ? <div className={styles.draftText}>{row.content}</div> : <ReactMarkdown skipHtml urlTransform={safeUrl}
              components={{ img: ({ alt }) => <span>{alt ? `[图片：${alt}]` : '[图片]'}</span>,
                a: ({ children, href }) => <a href={href} target="_blank" rel="noreferrer noopener">{children}</a> }}>
              {row.content}
            </ReactMarkdown>}
          </div>
          {(row.content_truncated || row.content_unavailable) && <p className={styles.historyPreview}>
            {row.provisional ? '较长的生成中内容暂未完整显示，完成后同步。' : row.detail ?
              '当前为消息预览，完整内容仍保存在会话中。' : '当前为消息预览。'}</p>}
          {(row.arguments_truncated || row.tool_calls_truncated) && <p className={styles.historyPreview}>工具调用信息未完整显示。</p>}
          {row.tool_calls?.map(call => {
            const result = results.get(call);
            const phase = toolStates[call.id];
            const correlated = phase && (phase.batch_id ? phase.batch_id === row.id :
              phase.run_id === row.run_id || phase.run_id === result?.run_id || (!result && phase.state === 'running'));
            return <ToolCall key={call.id} call={call} result={result} phase={correlated ? phase : undefined}
              initiallyOpen={!!result && standaloneResults.current.has(result.id) &&
                !!anchor.current?.rows.some(item => item.id === result.id)}
              stopAgent={stopAgent}
              openAgent={openAgent}
              agents={Object.values(agents).filter(agent => agent.source.parent_tool_call_id === call.id &&
                (!agent.parent_batch_id || agent.parent_batch_id === row.id) &&
                (!row.run_id || agent.source.parent_run_id === row.run_id))}
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
