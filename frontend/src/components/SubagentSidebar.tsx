import { useEffect, useRef } from 'react';
import type { RunView } from '../types';
import { WebApi } from '../api';
import { useSubagents } from '../useSubagents';
import type { SubagentList } from '../useSubagentList';
import Conversation from './Conversation';
import styles from './SubagentSidebar.module.css';

const labels: Record<string, string> = { running: '运行中', awaiting_approval: '等待审批', interrupted: '等待审批',
  stopping: '正在收尾', completed: '已完成', stopped: '已停止', failed: '失败', unconfirmed: '状态未确认' };

export default function SubagentSidebar({ api, sessionId, conversationRevision = 0, run, list, initialAgentId, close, stopAgent, showApprovals }: {
  conversationRevision?: number; api: WebApi; sessionId: string; run?: RunView | null; list: SubagentList; initialAgentId?: string;
  close: () => void; stopAgent: (path: string, originRunId: string) => void; showApprovals: () => void;
}) {
  const view = useSubagents(api, sessionId, list, run, initialAgentId);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => { closeButton.current?.focus(); }, []);
  const status = view.live?.status ?? view.record?.status;
  const live = view.live;
  const active = live && ['running', 'awaiting_approval', 'stopping', 'interrupted'].includes(live.status) &&
    run?.status === 'running' && live.source.parent_run_id === run.run_id && !run.done;
  const error = view.selected ? view.detailError : view.listError;

  return <aside className={styles.panel} aria-label="子智能体" onKeyDown={event => {
    if (event.key === 'Escape') { event.stopPropagation(); close(); }
  }}>
    <header className={styles.header}><strong>子智能体</strong><button ref={closeButton} className={styles.close}
      aria-label="关闭子智能体侧栏" onClick={close}>×</button></header>
    {view.selected ? <>
      <div className={styles.selection}>
        <div className={styles.selectionTop}><button className={styles.back} onClick={() => view.select()}>返回名称列表</button>
          {status && <span className={styles.status}>{labels[status]}</span>}</div>
        {view.record?.task && <p className={styles.task}>{view.record.task}</p>}
      </div>
      {error && <div className={styles.notice} role="alert">{error}<button onClick={view.retry}>重试</button></div>}
      {live?.messages?.some(row => row.content_unavailable) && <p className={styles.notice} role="status">
        较长的生成中消息将在完成后同步，当前可查看已保存内容。</p>}
      {view.loadingDetail && !view.messages.length && <p className={styles.notice} role="status">正在读取消息…</p>}
      {!view.loadingDetail && !view.messages.length && !error &&
        <p className={styles.notice}>暂无已保存消息。</p>}
      {!!view.messages.length && <Conversation loadReasoning={(row, offset) => api.messageReasoning(sessionId, row, offset, conversationRevision, view.selected)} loadChange={(row, patch) => api.fileChange(sessionId, row, patch, view.selected, conversationRevision)} key={`${sessionId}:${view.selected}`} messages={view.messages}
        history={view.history} contentScope={`${sessionId}:${conversationRevision}:${view.selected}`}
        loadContent={(row, signal) => api.messageContent(sessionId, row, conversationRevision, view.selected, signal)}
        compacted={view.stored?.has_compaction ?? false} toolStates={view.toolStates}
        stoppingRunId={live?.status === 'stopping' || run?.stop_requested ? live?.source.agent_run_id : undefined} />}
      <footer className={styles.footer}>
        <div>{status === 'awaiting_approval' || status === 'interrupted' ? <>
          <span>等待主会话审批</span><button className={styles.textButton} onClick={showApprovals}>到主会话处理</button>
        </> : <span>{status ? labels[status] : ''}</span>}</div>
        {active && <button className={styles.stop} disabled={status === 'stopping' || run?.stop_requested}
          onClick={() => stopAgent(live.source.agent_path, live.source.parent_run_id)}>
          {status === 'stopping' ? '正在停止…' : '停止该子智能体'}</button>}
      </footer>
    </> : <>
      <div className={styles.intro}>选择要查看的子智能体<p>同名的独立调用，用标识和任务区分。</p></div>
      {error && <div className={styles.notice} role="alert">{error}<button onClick={view.retry}>重试</button></div>}
      {view.loading && !view.records.length && <p className={styles.notice} role="status">正在读取列表…</p>}
      {!view.loading && !view.records.length && !error && <p className={styles.notice}>当前会话暂无子智能体。</p>}
      <div className={styles.list}>{view.records.map(row => <button key={row.agent_path} className={styles.row}
        aria-label={`查看子智能体 ${row.agent} · ${row.agent_id.slice(0, 6)}`} onClick={() => view.select(row.agent_id)}>
        <span className={styles.rowTop}><strong>{row.agent} · {row.agent_id.slice(0, 6)}</strong>
          <span className={styles.status}>{labels[row.status]}</span></span>
        {row.task && <span className={styles.rowTask}>{row.task}</span>}<span className={styles.arrow}>›</span>
      </button>)}</div>
      <footer className={styles.footer}><span>在主会话统一处理审批</span></footer>
    </>}
  </aside>;
}
