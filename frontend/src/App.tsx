import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { ScopeSelection } from './projectTypes';
import projectStyles from './components/ProjectNavigation.module.css';
import { browserConnection, WebApi } from './api';
import { useConversation } from './useConversation';
import ExecutionSelector from './components/ExecutionSelector';
import Sidebar from './components/Sidebar';
import Conversation from './components/Conversation';
import Composer from './components/Composer';
import ApprovalPanel from './components/ApprovalPanel';
import SubagentSidebar from './components/SubagentSidebar';
import ContextPanel, { ContextTrigger } from './components/ContextPanel';
import SessionUsage from './components/SessionUsage';
import SessionDiagnostics from './components/SessionDiagnostics';
import BackgroundTasks from './components/BackgroundTasks';
import ActivitySidebar, { type ActivityTab } from './components/ActivitySidebar';
import { useBackgroundTasks } from './useBackgroundTasks';
import WorkspaceLabel from './components/WorkspaceLabel';
import ModelMenu from './components/ModelMenu';
import { useContextView } from './useContext';
import { Icon } from './components/Icons';
import { runReconnectDelays } from './uiSettings';
import { useSubagentList } from './useSubagentList';
import { useTheme } from './useTheme';
import ThemeMenu from './components/ThemeMenu';
import { useSendScroll } from './useSendScroll';
import styles from './App.module.css';

export default function App({ api: supplied, active = true, selection, scopeName, scopePath, navigation: projectNavigation, preferences, projectEntry }: {
  api?: WebApi; active?: boolean; selection?: ScopeSelection; scopeName?: string; scopePath?: string;
  navigation?: (current: ReactNode) => ReactNode;
  preferences?: { theme: ReturnType<typeof useTheme>; sendScroll: ReturnType<typeof useSendScroll> };
  projectEntry?: (path?: string | null) => ReactNode;
}) {
  const api = useMemo(() => supplied ?? (() => { const c = browserConnection(); return new WebApi(c.token, c.scopeId, c.instanceId); })(), [supplied]);
  const chat = useConversation(api, active);
  const appliedSelection = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!active || !selection?.sessionId || appliedSelection.current === selection.revision ||
        chat.submitting || chat.changingControls || chat.changingModel || chat.selectingSession || chat.recovering || !chat.models.length) return;
    appliedSelection.current = selection.revision;
    void chat.selectSession(selection.sessionId);
  }, [active, selection, chat]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<ActivityTab>('agents');
  const background = useBackgroundTasks(api, sidebarOpen && sidebarTab === 'tasks', active);
  const sidebarTrigger = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLButtonElement | null>(null);
  const closeSidebar = () => { setSidebarOpen(false); returnFocus.current?.focus(); };
  const openSidebar = (tab: ActivityTab, fromContext = false) => {
    setModelMenu(false); setSidebarTab(tab); setSidebarOpen(true);
    returnFocus.current = fromContext ? contextTrigger.current : sidebarTrigger.current;
    if (tab === 'tasks') background.dismissNotice();
  };
  const agents = useSubagentList(api, chat.session?.session_id, chat.run, active);
  const localTheme = useTheme(!preferences), localSendScroll = useSendScroll();
  const theme = preferences?.theme ?? localTheme, sendScroll = preferences?.sendScroll ?? localSendScroll;
  const context = useContextView(api, chat.session, chat.run, active);
  const [modelMenu, setModelMenu] = useState(false);
  const closeModel = useCallback(() => setModelMenu(false), []);
  const contextTrigger = useRef<HTMLButtonElement>(null);
  const [navigation, setNavigation] = useState(false);
  useEffect(() => { if (!active) { setNavigation(false); setModelMenu(false); } }, [active]);
  const [agentPanel, setAgentPanel] = useState<{ sessionId: string; agentId?: string; revision: number } | null>(null);
  const approvalArea = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setAgentPanel(panel => panel?.sessionId === chat.session?.session_id && panel?.revision === (chat.session?.conversation_revision ?? 0) ? panel : null);
    setModelMenu(false);
  }, [chat.session?.session_id, chat.session?.conversation_revision]);
  const openAgent = (path?: string) => {
    openSidebar('agents');
    if (chat.session) setAgentPanel({ sessionId: chat.session.session_id, agentId: path?.split('/').at(-1), revision: chat.session.conversation_revision ?? 0 });
  };
  const busy = chat.run?.status === 'running' && !chat.run.done;
  const blocked = (!!busy && chat.run?.mode === 'compact') || (!busy && chat.session?.can_run === false) || chat.selectingSession || !chat.models.length || !!chat.run?.manual_review_required || !!chat.session?.manual_review_required || !!chat.session?.pending_approval;
  const controlsDisabled = chat.submitting || chat.changingControls || chat.changingModel || chat.selectingSession ||
    chat.recovering || !!chat.session?.recovery || !chat.models.length;
  const openModels = () => { setSidebarOpen(false); setModelMenu(true); };
  const labels = { running: chat.stopping ? '正在停止' : '正在处理', completed: '本次运行完成', stopped: '已停止',
    interrupted: '等待审批', failed: '本次运行失败', unconfirmed: '执行结果未同步' };

  return <div className={styles.app}>
    {active && <Sidebar rows={chat.sessions} selected={chat.session?.session_id} open={navigation}
      navigation={projectNavigation} scopeName={scopeName} scopeId={api.scopeId}
      currentSession={chat.session ?? undefined} currentRun={chat.run}
      loading={chat.listing.loading} failure={chat.listing.failure}
      retry={() => void chat.listing.refreshSessions().catch(() => console.warn('session_directory_retry_failed'))}
      disabled={chat.submitting || chat.changingControls || !chat.model} managing={chat.managingSessions}
      update={chat.updateSession} remove={chat.deleteSession} reorder={chat.reorderSessions}
      onNew={() => { void chat.newSession(); setNavigation(false); }}
      onSelect={id => { void chat.selectSession(id); setNavigation(false); }} onClose={() => setNavigation(false)} />}
    <main className={styles.workspace}>
      <header className={styles.header}>
        <div className={styles.headerLeft}>
          <button className={`${styles.iconButton} ${styles.mobileOnly}`} aria-label="打开会话栏" onClick={() => setNavigation(true)}><Icon name="menu" /></button>
          <ModelMenu models={chat.models} model={chat.session?.recovery ? chat.session.model_id : chat.model}
            level={chat.session?.thinking_level ?? 'model_default'} options={chat.thinkingOptions[chat.model]} disabled={controlsDisabled}
            open={modelMenu} toggle={() => modelMenu ? closeModel() : openModels()} close={closeModel}
            selectModel={value => void chat.selectModel(value)} selectThinking={value => void chat.selectThinking(value)} />
        </div>
        <div className={styles.headerActions}>
        <button ref={sidebarTrigger} className={styles.iconButton} aria-label="打开辅助侧栏" aria-expanded={sidebarOpen}
          aria-controls="activity-sidebar" title={`辅助侧栏 · 后台任务 ${background.activeCount ?? '状态未同步'}`}
          onClick={() => sidebarOpen ? closeSidebar() : openSidebar(sidebarTab)}>
          <Icon name="sidebar" />{(background.notice || (background.activeCount ?? 0) > 0) && <i className={styles.activityDot} />}</button>
        <ThemeMenu theme={theme.theme} select={theme.selectTheme} /></div>
      </header>
      <Conversation regenerate={() => void chat.regenerate()} lastReplyId={chat.session?.last_reply_id}
        scrollAfterSend={sendScroll.preference === 'latest'}
        lastSubmission={chat.lastSubmission?.session_id === chat.session?.session_id ? chat.lastSubmission : null}
        canRegenerate={!!chat.session?.can_run && !chat.submitting && !chat.selectingSession}
        key={chat.session?.session_id ?? 'new'} messages={chat.messages} history={chat.history}
        contentScope={`${chat.session?.session_id}:${chat.session?.conversation_revision ?? 0}`}
        loadContent={(row, signal) => api.messageContent(chat.session!.session_id, row, chat.session?.conversation_revision ?? 0, undefined, signal)}
        loadReasoning={(row, offset) => api.messageReasoning(chat.session!.session_id, row, offset, chat.session?.conversation_revision ?? 0)}
        loadChange={(row, patch) => api.fileChange(chat.session!.session_id, row, patch, undefined, chat.session?.conversation_revision ?? 0)}
        compacted={chat.session?.has_compaction ?? false} toolStates={chat.run?.tool_states}
        agents={chat.run?.agents}
        stopAgent={(path, runId, agentRunId) => void chat.stopAgent(path, runId, agentRunId)}
        openAgent={path => openAgent(path)}
        recovery={chat.session?.recovery}
        recovering={chat.recovering} onRecover={action => void chat.recover(action)}
        stoppingRunId={chat.run?.status === 'running' && chat.run.stop_requested ? chat.run.run_id : undefined} />
      <div className={styles.feedback} aria-live="polite">
        {chat.session?.execution?.kind==='sandbox'&&chat.session.execution.state!=='ready'&&<span className={styles.error}>沙箱尚不可执行。请在执行位置菜单核对原环境；历史仍可查看。</span>}
        {chat.session?.execution_root_error && <span className={styles.error}>执行目录不可用：{{ missing: '目录不存在', not_directory: '路径不是目录', not_registered: '执行副本未登记', wrong_project: '项目身份不匹配', unreadable: '无法访问或检查执行目录' }[chat.session.execution_root_error.reason]}。历史仍可查看。</span>}
        {background.notice && <button className={styles.syncButton} onClick={() => openSidebar('tasks')}>{background.notice}</button>}
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
      <Composer sendScroll={sendScroll} workspaceEntry={<>
        {projectEntry ? projectEntry(chat.session ? chat.session.workspace_root : scopePath) : scopeName ? <div className={projectStyles.scope} role="status" aria-label="当前项目"
          title={chat.session?.workspace_root ?? scopePath}><strong>{scopeName}</strong></div> : <WorkspaceLabel api={api} session={chat.session} />}
        {/^[0-9a-f-]{36}$/.test(api.scopeId)&&<ExecutionSelector api={api} modelId={chat.model} session={chat.session}
          disabled={chat.submitting||chat.selectingSession} running={!!busy} create={chat.newSession}
          refresh={async()=>{if(chat.session)await chat.selectSession(chat.session.session_id);}} />}
      </>} contextEntry={<ContextTrigger value={context.value} open={sidebarOpen && sidebarTab === 'context'}
        disabled={!chat.session || chat.selectingSession} triggerRef={contextTrigger}
        toggle={() => sidebarOpen && sidebarTab === 'context' ? closeSidebar() : openSidebar('context', true)} />} text={chat.draft} pending={chat.pending} pendingMode={chat.pendingMode} busy={!!busy}
        stopping={chat.stopping} sending={chat.submitting || chat.changingControls || chat.changingModel} blocked={blocked} setText={chat.setDraft}
        setPending={chat.setPending} send={value => void chat.submit(value)} stop={() => void chat.stop()}
        api={api} session={chat.session} selectMode={value => void chat.selectPermissionMode(value)}
        openModel={openModels}
        compact={() => void chat.compact()} openContext={() => openSidebar('context', true)}
        openAgents={() => openAgent()}
        selectTools={names => void chat.selectTools(names)} controlsDisabled={controlsDisabled} />
    </main>
    {active && sidebarOpen && <ActivitySidebar tab={sidebarTab} select={tab => { setSidebarTab(tab); if (tab === 'tasks') background.dismissNotice(); }}
      activeCount={background.activeCount} close={closeSidebar}>{ {
      tasks: <BackgroundTasks state={background} />,
      context: chat.session ? <ContextPanel value={context.value} loading={context.loading} error={context.error}
        retry={context.retry} usageContent={<SessionUsage api={api} sessionId={chat.session.session_id} run={chat.run} />}
        diagnosticContent={<SessionDiagnostics api={api} sessionId={chat.session.session_id} run={chat.run} />} /> :
        <p className={styles.sidebarEmpty}>选择会话后查看上下文。</p>,
      agents: chat.session ? <SubagentSidebar key={`${chat.session.session_id}:${chat.session.conversation_revision ?? 0}`}
        conversationRevision={chat.session.conversation_revision ?? 0} list={agents} api={api}
        sessionId={chat.session.session_id} run={chat.run}
        initialAgentId={agentPanel?.sessionId === chat.session.session_id && agentPanel.revision === (chat.session.conversation_revision ?? 0) ? agentPanel.agentId : undefined}
        stopAgent={(path, runId, agentRunId) => void chat.stopAgent(path, runId, agentRunId)} showApprovals={() => {
          if (window.matchMedia('(max-width: 1100px)').matches) setSidebarOpen(false);
          approvalArea.current?.scrollIntoView({ block: 'nearest' }); approvalArea.current?.focus();
        }} /> : <p className={styles.sidebarEmpty}>选择会话后查看子智能体。</p>,
    } }</ActivitySidebar>}
  </div>;
}
