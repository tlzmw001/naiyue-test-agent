import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { browserToken, WebApi } from './api';
import { useConversation } from './useConversation';
import Sidebar from './components/Sidebar';
import Conversation from './components/Conversation';
import Composer from './components/Composer';
import ApprovalPanel from './components/ApprovalPanel';
import SubagentSidebar from './components/SubagentSidebar';
import ContextPanel, { ContextTrigger } from './components/ContextPanel';
import SessionUsage from './components/SessionUsage';
import WorkspaceLabel from './components/WorkspaceLabel';
import ModelMenu from './components/ModelMenu';
import { useContextView } from './useContext';
import { Icon } from './components/Icons';
import { runReconnectDelays } from './uiSettings';
import { useSubagentList } from './useSubagentList';
import { useTheme } from './useTheme';
import ThemeMenu from './components/ThemeMenu';
import styles from './App.module.css';

export default function App({ api: supplied }: { api?: WebApi }) {
  const api = useMemo(() => supplied ?? new WebApi(browserToken()), [supplied]);
  const chat = useConversation(api);
  const agents = useSubagentList(api, chat.session?.session_id, chat.run);
  const theme = useTheme();
  const context = useContextView(api, chat.session, chat.run);
  const [contextPanel, setContextPanel] = useState(false);
  const [modelMenu, setModelMenu] = useState(false);
  const closeModel = useCallback(() => setModelMenu(false), []);
  const contextTrigger = useRef<HTMLButtonElement>(null);
  const closeContext = () => { setContextPanel(false); contextTrigger.current?.focus(); };
  const [navigation, setNavigation] = useState(false);
  const [agentPanel, setAgentPanel] = useState<{ sessionId: string; agentId?: string; revision: number } | null>(null);
  const agentTrigger = useRef<HTMLButtonElement>(null);
  const approvalArea = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setAgentPanel(panel => panel?.sessionId === chat.session?.session_id && panel?.revision === (chat.session?.conversation_revision ?? 0) ? panel : null);
    setContextPanel(false); setModelMenu(false);
  }, [chat.session?.session_id, chat.session?.conversation_revision]);
  const closeAgents = () => { setAgentPanel(null); agentTrigger.current?.focus(); };
  const openAgent = (path?: string) => {
    setModelMenu(false);
    setContextPanel(false);
    if (chat.session) setAgentPanel({ sessionId: chat.session.session_id, agentId: path?.split('/').at(-1), revision: chat.session.conversation_revision ?? 0 });
  };
  const busy = chat.run?.status === 'running' && !chat.run.done;
  const blocked = (!!busy && chat.run?.mode === 'compact') || (!busy && chat.session?.can_run === false) || chat.selectingSession || !chat.models.length || !!chat.run?.manual_review_required || !!chat.session?.manual_review_required || !!chat.session?.pending_approval;
  const controlsDisabled = chat.submitting || chat.changingControls || chat.changingModel || chat.selectingSession ||
    chat.recovering || !!chat.session?.recovery || !chat.models.length;
  const openModels = () => { setContextPanel(false); setAgentPanel(null); setModelMenu(true); };
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
          <ModelMenu models={chat.models} model={chat.session?.recovery ? chat.session.model_id : chat.model}
            level={chat.session?.thinking_level ?? 'model_default'} options={chat.thinkingOptions[chat.model]} disabled={controlsDisabled}
            open={modelMenu} toggle={() => modelMenu ? closeModel() : openModels()} close={closeModel}
            selectModel={value => void chat.selectModel(value)} selectThinking={value => void chat.selectThinking(value)} />
        </div>
        <div className={styles.headerActions}><WorkspaceLabel api={api} session={chat.session} />
        {!!agents.records.length && <button ref={agentTrigger} className={styles.agentsButton} aria-label="打开子智能体侧栏"
          disabled={!chat.session || chat.selectingSession} aria-expanded={!!agentPanel}
          onClick={() => agentPanel ? closeAgents() : openAgent()}>子智能体</button>}
        {!agents.records.length && agents.error && <button className={styles.syncButton}
          aria-label="重试读取子智能体列表" onClick={agents.retry}>重试子智能体</button>}
        <ThemeMenu theme={theme.theme} select={theme.selectTheme} /></div>
      </header>
      <Conversation regenerate={() => void chat.regenerate()} lastReplyId={chat.session?.last_reply_id}
        canRegenerate={!!chat.session?.can_run && !chat.submitting && !chat.selectingSession}
        key={chat.session?.session_id ?? 'new'} messages={chat.messages} history={chat.history}
        contentScope={`${chat.session?.session_id}:${chat.session?.conversation_revision ?? 0}`}
        loadContent={(row, signal) => api.messageContent(chat.session!.session_id, row, chat.session?.conversation_revision ?? 0, undefined, signal)}
        loadReasoning={(row, offset) => api.messageReasoning(chat.session!.session_id, row, offset, chat.session?.conversation_revision ?? 0)}
        loadChange={(row, patch) => api.fileChange(chat.session!.session_id, row, patch, undefined, chat.session?.conversation_revision ?? 0)}
        compacted={chat.session?.has_compaction ?? false} toolStates={chat.run?.tool_states}
        agents={chat.run?.agents}
        stopAgent={(path, runId) => void chat.stopAgent(path, runId)}
        openAgent={path => openAgent(path)}
        recovery={chat.session?.recovery}
        recovering={chat.recovering} onRecover={action => void chat.recover(action)}
        stoppingRunId={chat.run?.status === 'running' && chat.run.stop_requested ? chat.run.run_id : undefined} />
      <div className={styles.feedback} aria-live="polite">
        {chat.run && chat.run.status !== 'completed' && <span className={`${styles.runStatus} ${busy ? styles.running : ''}`}><i />{busy && chat.run.mode === 'compact' ? '正在压缩上下文' : labels[chat.run.status]}</span>}
        {!!chat.connection?.attempt && !chat.connection.failed && <span role="status">正在恢复连接（{chat.connection.attempt}/{runReconnectDelays.length}）</span>}
        {chat.connection?.failed && <span className={styles.error}>连接恢复未完成，已保留当前内容。</span>}
        {chat.run?.manual_review_required && <span className={styles.error}>执行结果需要人工核对，当前不能继续。</span>}
        {chat.run?.incomplete && <span className={styles.error}>部分消息尚未同步</span>}
        {chat.notice && <span>{chat.notice}</span>}
        {chat.run && (chat.run.status !== 'completed' || chat.run.incomplete || chat.connection?.failed) &&
          <button className={styles.syncButton} onClick={chat.reconnect}>重新同步</button>}
      </div>
      {!!chat.session?.approval_batches?.length && <div ref={approvalArea} tabIndex={-1} className={styles.approvalArea}>
        {chat.session.approval_batches.map(batch => <ApprovalPanel key={batch.approval_batch_id} batch={batch}
          disabled={chat.submitting || chat.selectingSession || !!chat.session?.manual_review_required}
          stop={() => void chat.stopApproval(batch)}
          submit={(decisions, remember) => chat.approve(batch, decisions, remember)} />)}
      </div>}
      <Composer contextEntry={<ContextTrigger value={context.value} open={contextPanel}
        disabled={!chat.session || chat.selectingSession} triggerRef={contextTrigger}
        toggle={() => { closeModel(); setAgentPanel(null); setContextPanel(value => !value); }} />} text={chat.draft} pending={chat.pending} pendingMode={chat.pendingMode} busy={!!busy}
        stopping={chat.stopping} sending={chat.submitting || chat.changingControls || chat.changingModel} blocked={blocked} setText={chat.setDraft}
        setPending={chat.setPending} send={value => void chat.submit(value)} stop={() => void chat.stop()}
        api={api} session={chat.session} selectMode={value => void chat.selectPermissionMode(value)}
        openModel={openModels}
        compact={() => void chat.compact()} openContext={() => { setAgentPanel(null); setContextPanel(true); }}
        openAgents={() => openAgent()}
        selectTools={names => void chat.selectTools(names)} controlsDisabled={controlsDisabled} />
    </main>
    {contextPanel && <ContextPanel value={context.value} loading={context.loading} error={context.error}
      retry={context.retry} close={closeContext} usageContent={chat.session && <SessionUsage
        api={api} sessionId={chat.session.session_id} run={chat.run} />} />}
    {agentPanel && agentPanel.sessionId === chat.session?.session_id && agentPanel.revision === (chat.session?.conversation_revision ?? 0) && <SubagentSidebar conversationRevision={chat.session?.conversation_revision ?? 0} list={agents} api={api}
      sessionId={agentPanel.sessionId} run={chat.run} initialAgentId={agentPanel.agentId}
      close={closeAgents} stopAgent={(path, runId) => void chat.stopAgent(path, runId)}
      showApprovals={() => {
        if (window.matchMedia('(max-width: 1100px)').matches) setAgentPanel(null);
        approvalArea.current?.scrollIntoView({ block: 'nearest' }); approvalArea.current?.focus();
      }} />}
  </div>;
}
