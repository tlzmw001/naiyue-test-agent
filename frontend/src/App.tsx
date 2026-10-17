import { useEffect, useMemo, useRef, useState } from 'react';
import { browserToken, WebApi } from './api';
import { useConversation } from './useConversation';
import Sidebar from './components/Sidebar';
import Conversation from './components/Conversation';
import Composer from './components/Composer';
import ApprovalPanel from './components/ApprovalPanel';
import SubagentSidebar from './components/SubagentSidebar';
import ContextPanel, { ContextTrigger } from './components/ContextPanel';
import { useContextView } from './useContext';
import { Icon } from './components/Icons';
import styles from './App.module.css';

export default function App({ api: supplied }: { api?: WebApi }) {
  const api = useMemo(() => supplied ?? new WebApi(browserToken()), [supplied]);
  const chat = useConversation(api);
  const context = useContextView(api, chat.session, chat.run);
  const [contextPanel, setContextPanel] = useState(false);
  const contextTrigger = useRef<HTMLButtonElement>(null);
  const closeContext = () => { setContextPanel(false); contextTrigger.current?.focus(); };
  const [navigation, setNavigation] = useState(false);
  const [agentPanel, setAgentPanel] = useState<{ sessionId: string; agentId?: string } | null>(null);
  const agentTrigger = useRef<HTMLButtonElement>(null);
  const approvalArea = useRef<HTMLDivElement>(null);
  useEffect(() => { setAgentPanel(null); setContextPanel(false); }, [chat.session?.session_id]);
  const closeAgents = () => { setAgentPanel(null); agentTrigger.current?.focus(); };
  const openAgent = (path?: string) => {
    setContextPanel(false);
    if (chat.session) setAgentPanel({ sessionId: chat.session.session_id, agentId: path?.split('/').at(-1) });
  };
  const busy = chat.run?.status === 'running' && !chat.run.done;
  const blocked = (!!busy && chat.run?.mode === 'compact') || (!busy && chat.session?.can_run === false) || chat.selectingSession || !chat.models.length || !!chat.run?.manual_review_required || !!chat.session?.manual_review_required || !!chat.session?.pending_approval;
  const labels = { running: chat.stopping ? '正在停止' : '正在处理', completed: '本次运行完成', stopped: '已停止',
    interrupted: '等待审批', failed: '本次运行失败', unconfirmed: '执行结果未同步' };

  return <div className={styles.app}>
    <Sidebar rows={chat.sessions} selected={chat.session?.session_id} open={navigation}
      disabled={chat.submitting || chat.changingControls || !chat.model} managing={chat.managingSessions}
      update={chat.updateSession} remove={chat.deleteSession} reorder={chat.reorderSessions}
      onNew={() => { void chat.newSession(); setNavigation(false); }}
      onSelect={id => { void chat.selectSession(id); setNavigation(false); }} onClose={() => setNavigation(false)} />
    <main className={styles.workspace}>
      <header className={styles.header}>
        <div className={styles.headerLeft}>
          <button className={`${styles.iconButton} ${styles.mobileOnly}`} aria-label="打开会话栏" onClick={() => setNavigation(true)}><Icon name="menu" /></button>
        </div>
        <div className={styles.headerActions}><div className={styles.workspacePath} title={chat.session?.workspace_root ?? ''}>
          {chat.session?.workspace_root ?? '未设置工作区'}
        </div><button ref={agentTrigger} className={styles.agentsButton} aria-label="打开子智能体侧栏"
          disabled={!chat.session || chat.selectingSession} aria-expanded={!!agentPanel}
          onClick={() => agentPanel ? closeAgents() : openAgent()}>子智能体</button></div>
      </header>
      <Conversation messages={chat.messages} compacted={chat.session?.has_compaction ?? false} toolStates={chat.run?.tool_states}
        agents={chat.run?.agents}
        stopAgent={(path, runId) => void chat.stopAgent(path, runId)}
        openAgent={path => openAgent(path)}
        recovery={chat.session?.recovery}
        recovering={chat.recovering} onRecover={action => void chat.recover(action)}
        stoppingRunId={chat.run?.status === 'running' && chat.run.stop_requested ? chat.run.run_id : undefined} />
      <div className={styles.feedback} aria-live="polite">
        {chat.run && <span className={`${styles.runStatus} ${busy ? styles.running : ''}`}><i />{busy && chat.run.mode === 'compact' ? '正在压缩上下文' : labels[chat.run.status]}</span>}
        {chat.run?.manual_review_required && <span className={styles.error}>执行结果需要人工核对，当前不能继续。</span>}
        {chat.run?.incomplete && <span className={styles.error}>部分消息尚未同步</span>}
        {chat.notice && <span>{chat.notice}</span>}
        {chat.run && <button className={styles.syncButton} onClick={chat.reconnect}>重新同步</button>}
      </div>
      {!!chat.session?.approval_batches?.length && <div ref={approvalArea} tabIndex={-1} className={styles.approvalArea}>
        {chat.session.approval_batches.map(batch => <ApprovalPanel key={batch.approval_batch_id} batch={batch}
          disabled={chat.submitting || chat.selectingSession || !!chat.session?.manual_review_required}
          stop={() => void chat.stopApproval(batch)}
          submit={(decisions, remember) => void chat.approve(batch, decisions, remember)} />)}
      </div>}
      <Composer contextEntry={<ContextTrigger value={context.value} open={contextPanel}
        disabled={!chat.session || chat.selectingSession} triggerRef={contextTrigger}
        toggle={() => { setAgentPanel(null); setContextPanel(value => !value); }} />} text={chat.draft} pending={chat.pending} pendingMode={chat.pendingMode} busy={!!busy}
        stopping={chat.stopping} sending={chat.submitting || chat.changingControls || chat.changingModel} blocked={blocked} setText={chat.setDraft}
        setPending={chat.setPending} send={value => void chat.submit(value)} stop={() => void chat.stop()}
        api={api} session={chat.session} selectMode={value => void chat.selectPermissionMode(value)}
        models={chat.models} model={chat.model} thinkingOptions={chat.thinkingOptions[chat.model]}
        selectModel={value => void chat.selectModel(value)} selectThinking={value => void chat.selectThinking(value)}
        compact={() => void chat.compact()} openContext={() => { setAgentPanel(null); setContextPanel(true); }}
        openAgents={() => openAgent()}
        selectTools={names => void chat.selectTools(names)} controlsDisabled={chat.submitting || chat.changingControls ||
          chat.changingModel || chat.selectingSession || chat.recovering || !!chat.session?.recovery || !chat.models.length} />
    </main>
    {contextPanel && <ContextPanel value={context.value} loading={context.loading} error={context.error}
      retry={context.retry} close={closeContext} />}
    {agentPanel && agentPanel.sessionId === chat.session?.session_id && <SubagentSidebar api={api}
      sessionId={agentPanel.sessionId} run={chat.run} initialAgentId={agentPanel.agentId}
      close={closeAgents} stopAgent={(path, runId) => void chat.stopAgent(path, runId)}
      showApprovals={() => {
        if (window.matchMedia('(max-width: 1100px)').matches) setAgentPanel(null);
        approvalArea.current?.scrollIntoView({ block: 'nearest' }); approvalArea.current?.focus();
      }} />}
  </div>;
}
