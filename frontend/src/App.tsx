import { useMemo, useState } from 'react';
import { browserToken, WebApi } from './api';
import { useConversation } from './useConversation';
import Sidebar from './components/Sidebar';
import Conversation from './components/Conversation';
import Composer from './components/Composer';
import ApprovalPanel from './components/ApprovalPanel';
import { Icon } from './components/Icons';
import styles from './App.module.css';

export default function App({ api: supplied }: { api?: WebApi }) {
  const api = useMemo(() => supplied ?? new WebApi(browserToken()), [supplied]);
  const chat = useConversation(api);
  const [navigation, setNavigation] = useState(false);
  const busy = chat.run?.status === 'running' && !chat.run.done;
  const blocked = (!busy && chat.session?.can_run === false) || chat.selectingSession || !chat.models.length || !!chat.run?.manual_review_required || !!chat.session?.manual_review_required || !!chat.session?.pending_approval;
  const labels = { running: chat.stopping ? '正在停止' : '正在处理', completed: '本次运行完成', stopped: '已停止',
    interrupted: '等待审批', failed: '本次运行失败', unconfirmed: '执行结果未同步' };

  return <div className={styles.app}>
    <Sidebar rows={chat.sessions} selected={chat.session?.session_id} open={navigation}
      disabled={chat.submitting || !chat.model} onNew={() => { void chat.newSession(); setNavigation(false); }}
      onSelect={id => { void chat.selectSession(id); setNavigation(false); }} onClose={() => setNavigation(false)} />
    <main className={styles.workspace}>
      <header className={styles.header}>
        <div className={styles.headerLeft}>
          <button className={`${styles.iconButton} ${styles.mobileOnly}`} aria-label="打开会话栏" onClick={() => setNavigation(true)}><Icon name="menu" /></button>
          <select aria-label="模型" value={chat.session?.recovery ? chat.session.model_id : chat.model} onChange={event => void chat.selectModel(event.target.value)}
            disabled={!!chat.session?.recovery || chat.recovering || chat.selectingSession || chat.changingModel || !chat.models.length}>
            {!chat.models.length && <option value="">加载模型…</option>}
            {chat.session?.recovery && !chat.models.includes(chat.session.model_id) &&
              <option value={chat.session.model_id}>{chat.session.model_id}（已保存）</option>}
            {chat.models.map(id => <option key={id} value={id}>{id}</option>)}
          </select>
        </div>
        <div className={styles.workspacePath} title={chat.session?.workspace_root ?? ''}>
          {chat.session?.workspace_root ?? '未设置工作区'}
        </div>
      </header>
      <Conversation messages={chat.messages} compacted={chat.session?.has_compaction ?? false} toolStates={chat.run?.tool_states}
        recovery={chat.session?.recovery}
        recovering={chat.recovering} onRecover={action => void chat.recover(action)}
        stoppingRunId={chat.run?.status === 'running' && chat.run.stop_requested ? chat.run.run_id : undefined} />
      <div className={styles.feedback} aria-live="polite">
        {chat.run && <span className={`${styles.runStatus} ${busy ? styles.running : ''}`}><i />{labels[chat.run.status]}</span>}
        {chat.run?.manual_review_required && <span className={styles.error}>执行结果需要人工核对，当前不能继续。</span>}
        {chat.run?.incomplete && <span className={styles.error}>部分消息尚未同步</span>}
        {chat.notice && <span>{chat.notice}</span>}
        {chat.run && <button className={styles.syncButton} onClick={chat.reconnect}>重新同步</button>}
      </div>
      {!busy && chat.session?.pending_approval && chat.session.approval_run_id && !!chat.session.approval_requests?.length &&
        <div className={styles.approvalArea}><ApprovalPanel
          key={chat.session.approval_run_id} origin={chat.session.approval_run_id} requests={chat.session.approval_requests}
          disabled={chat.submitting || chat.selectingSession || !!chat.session.manual_review_required}
          submit={items => void chat.approve(items)} /></div>}
      <Composer text={chat.draft} pending={chat.pending} pendingMode={chat.pendingMode} busy={!!busy}
        stopping={chat.stopping} sending={chat.submitting} blocked={blocked} setText={chat.setDraft}
        setPending={chat.setPending} send={value => void chat.submit(value)} stop={() => void chat.stop()} />
    </main>
  </div>;
}
