import { useEffect, useRef, useState } from 'react';
import { errorText, WebApi } from './api';
import { subscribeRun } from './runSubscription';
import type { ApprovalBatch, PermissionMode, RunView, SessionView, ThinkingOptions, ToolDecision } from './types';
import { useSessions } from './useSessions';
import { useMessageHistory } from './useMessageHistory';
import { stopObservedSubagent } from './subagentControls';

export function useConversation(api: WebApi) {
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [thinkingOptions, setThinkingOptions] = useState<Record<string, ThinkingOptions>>({});
  const [session, setSession] = useState<SessionView | null>(null);
  const history = useMessageHistory(api);
  const messages = history.messages;
  const [run, setRun] = useState<RunView | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Record<string, string[]>>({});
  const [notice, setNotice] = useState('');
  const [lastSubmission, setLastSubmission] = useState<{ session_id: string } | null>(null);
  const [connection, setConnection] = useState<{ session_id: string; run_id: string; attempt: number; failed: boolean } | null>(null);
  const listing = useSessions(api, setNotice);
  const { sessions, refreshSessions } = listing;
  const [submitting, setSubmitting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [changingModel, setChangingModel] = useState(false);
  const [selectingSession, setSelectingSession] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [changingControls, setChangingControls] = useState(false);
  const selectionGeneration = useRef(0);
  const controlsRevision = useRef(0);
  const approvalRevision = useRef(0);
  const selectionPending = useRef(false);
  const selected = useRef<string>('new');
  const currentRun = useRef<RunView | null>(null);
  const draftRef = useRef<Record<string, string>>({});
  const pendingRef = useRef<Record<string, string[]>>({});
  const stoppedMode = useRef<Record<string, boolean>>({});
  const busySend = useRef(false);
  const subscription = useRef<AbortController | null>(null);
  const pendingStop = useRef<string | null>(null);

  const writeDraft = (value: string, id = selected.current) => {
    draftRef.current = { ...draftRef.current, [id]: value };
    setDrafts(draftRef.current);
  };
  const writePending = (value: string[], id = selected.current) => {
    pendingRef.current = { ...pendingRef.current, [id]: value };
    setPending(pendingRef.current);
  };

  async function refreshSession(sid: string, signal?: AbortSignal, prepare = false): Promise<SessionView> {
    const revision = controlsRevision.current, generation = selectionGeneration.current, approvals = approvalRevision.current;
    const value = await (prepare ? api.prepare(sid, signal) : api.session(sid, signal));
    if (!signal?.aborted && selected.current === sid && generation === selectionGeneration.current) {
      if (approvals !== approvalRevision.current) return refreshSession(sid, signal, prepare);
      setSession(previous =>
      previous?.session_id === sid && revision !== controlsRevision.current ? {
        ...value, active_tools: previous.active_tools, permission_mode: previous.permission_mode,
        model_id: previous.model_id, thinking_level: previous.thinking_level,
      } : value);
      await history.synchronize({ ...value.history, messages: value.messages });
    }
    return value;
  }

  useEffect(() => {
    let disposed = false;
    Promise.all([api.models(), refreshSessions()]).then(([config]) => {
      if (!disposed) { setModels(config.model_ids); setModel(config.default_model_id); setThinkingOptions(config.thinking_options ?? {}); }
    }).catch(error => { if (!disposed) setNotice(errorText(error)); });
    return () => { disposed = true; subscription.current?.abort(); };
  }, [api]);

  function display(view: RunView, sid: string) {
    if (selected.current !== sid) return;
    currentRun.current = view;
    setRun(view);
    history.merge(view.messages);
    if (view.mode === 'compact' && view.status === 'completed' && view.compaction_result) {
      const status = view.compaction_result.status;
      setNotice(status === 'applied' ? '上下文已压缩，原任务不会自动继续。' : status === 'skipped' ?
        '当前暂无可压缩的早期对话，已保留全部上下文。' : '压缩未成功，原上下文已保留，可以继续对话或重试。');
    }
    setStopping(view.status === 'running' && (view.stop_requested === true || pendingStop.current === view.run_id));
    if (view.status !== 'running') {
      setStopping(false);
      if (view.status === 'stopped') setNotice('已停止，保留内容可由你重新发送。');
    }
  }

  async function watch(runId: string, sid: string, initial?: RunView, resynchronize = false) {
    if (selected.current !== sid) return;
    subscription.current?.abort();
    const controller = new AbortController();
    subscription.current = controller;
    let attempts = 0;
    setConnection({ session_id: sid, run_id: runId, attempt: 0, failed: false });
    try {
      const confirmed = await subscribeRun({ api, runId, sessionId: sid, signal: controller.signal, initial, resynchronize,
        synchronize: () => refreshSession(sid, controller.signal, true),
        reconnecting: attempt => {
          attempts = attempt;
          if (!controller.signal.aborted && selected.current === sid)
            setConnection({ session_id: sid, run_id: runId, attempt, failed: false });
        },
        update: (view, event) => {
          if (controller.signal.aborted || selected.current !== sid) return;
          display(view, sid);
          setConnection(previous => previous?.attempt || previous?.failed ?
            { session_id: sid, run_id: runId, attempt: 0, failed: false } : previous);
          if (event?.event_type === 'approval_requested') {
            void refreshSession(sid, controller.signal).catch(error => {
              if (!controller.signal.aborted && selected.current === sid) setNotice(errorText(error));
            });
          }
          if (event && !event.source && event.event_type === 'control_delivered') setNotice('补充消息已加入本次上下文。');
        },
      });
      if (!controller.signal.aborted && confirmed) {
        await refreshSession(sid, controller.signal);
        await refreshSessions();
      }
    } catch (error) {
      if (!controller.signal.aborted && selected.current === sid) {
        console.warn('run_subscription_failed');
        setConnection({ session_id: sid, run_id: runId, attempt: attempts, failed: true });
        setNotice(errorText(error));
      }
    }
  }

  function applySession(value: SessionView, replace = false) {
    const id = value.session_id;
    selected.current = id;
    setSession(value);
    setModel(models.includes(value.model_id) ? value.model_id : models[0] ?? '');
    if (replace || history.sessionId !== id) history.open(id, undefined, { ...value.history, messages: value.messages });
    else void history.synchronize({ ...value.history, messages: value.messages });
    currentRun.current = value.live;
    setRun(value.live);
    setStopping(value.live?.status === 'running' && value.live.stop_requested === true);
    if (value.live?.status === 'stopped') {
      stoppedMode.current[id] = true;
      if (!(id in pendingRef.current)) writePending(value.live.retained ?? [], id);
    }
    if (value.live && !value.live.done) void watch(value.live.run_id, id, value.live);
  }

  async function selectSession(id: string) {
    if (busySend.current) return;
    subscription.current?.abort();
    history.cancel();
    setConnection(null);
    const generation = ++selectionGeneration.current;
    selectionPending.current = true;
    setSelectingSession(true);
    setNotice('');
    try {
      const value = await api.prepare(id);
      if (generation !== selectionGeneration.current) return;
      applySession(value, true);
      if (value.recovery?.kind === 'interrupted_execution' && value.recovery.actions.includes('continue') ||
          value.recovery?.kind === 'pending_approval' && value.recovery.actions.includes('restore_approval'))
        await recover(value);
    } catch (error) {
      if (generation === selectionGeneration.current) setNotice(errorText(error));
    } finally {
      if (generation === selectionGeneration.current) { selectionPending.current = false; setSelectingSession(false); }
    }
  }

  async function newSession() {
    if (busySend.current || selectionPending.current || !model) return;
    setNotice('');
    try {
      const created = await api.create(model);
      await selectSession(created.session_id);
      await refreshSessions();
    } catch (error) { setNotice(errorText(error)); }
  }

  async function deleteSession(sid: string) {
    if (busySend.current || selectionPending.current) return false;
    busySend.current = true;
    try {
      if (!await listing.deleteSession(sid)) return false;
      delete draftRef.current[sid]; delete pendingRef.current[sid]; delete stoppedMode.current[sid];
      setDrafts({ ...draftRef.current }); setPending({ ...pendingRef.current });
      if (selected.current === sid) {
        subscription.current?.abort(); selectionGeneration.current++; controlsRevision.current++;
        selected.current = 'new'; currentRun.current = null;
        setSession(null); history.reset(); setRun(null); setStopping(false);
      }
      return true;
    } finally { busySend.current = false; }
  }

  async function recover(value: SessionView | null, action: 'continue' | 'restore_approval' =
      value?.recovery?.kind === 'pending_approval' ? 'restore_approval' : 'continue') {
    if (busySend.current || !value?.checkpoint_id || !value.recovery?.actions.includes(action)) return;
    const sid = value.session_id;
    busySend.current = true;
    setSubmitting(true);
    setRecovering(true);
    setNotice('');
    try {
      const ready = await api.recover(sid, value.checkpoint_id, action);
      if (selected.current === sid) {
        applySession(ready);
        stoppedMode.current[sid] = false;
        setNotice(action === 'restore_approval' ? '已恢复待审批批次，请核对后决定；工具尚未执行。' :
          '旧运行已结束。历史已保留，发送新消息后才会开始运行。');
      }
      try { await refreshSessions(); }
      catch { setNotice(action === 'restore_approval' ? '待审批已恢复，会话列表刷新未确认。请核对当前审批。' :
        '旧运行已结束。会话列表刷新未确认，可继续输入新消息。'); }
    } catch (error) {
      if (selected.current === sid) {
        setNotice(errorText(error));
        try { applySession(await api.prepare(sid)); }
        catch { setNotice(errorText(error) + ' 当前历史刷新未确认，请重新选择会话。'); }
      }
    } finally {
      busySend.current = false;
      setSubmitting(false);
      setRecovering(false);
    }
  }

  async function selectModel(value: string) {
    if (changingModel || selectionPending.current || recovering || session?.recovery) return;
    if (!session) { setModel(value); return; }
    const sid = session.session_id;
    controlsRevision.current++;
    setChangingModel(true);
    try {
      await api.select(sid, value);
      if (selected.current !== sid) { await refreshSessions(); return; }
      setModel(value);
      controlsRevision.current++;
      setSession(previous => previous ? { ...previous, model_id: value, thinking_level: 'model_default' } : previous);
      await refreshSessions();
      setNotice('模型已切换，下一次模型调用生效。');
    } catch (error) {
      if (selected.current === sid) {
        setNotice(errorText(error));
        try {
          const actual = await api.session(sid);
          if (selected.current === sid) {
            controlsRevision.current++;
            setModel(actual.model_id);
            setSession(previous => previous?.session_id === sid ? { ...previous,
              model_id: actual.model_id, thinking_level: actual.thinking_level } : previous);
          }
        } catch { if (selected.current === sid) setNotice(errorText(error) + ' 当前模型刷新未确认，请重新打开会话核对。'); }
      }
    } finally { setChangingModel(false); }
  }

  async function changeControls(change: { active_tools: string[] } | { permission_mode: PermissionMode } | { thinking_level: string }) {
    if (busySend.current || selectionPending.current || recovering || session?.recovery || !model) return;
    busySend.current = true;
    controlsRevision.current++;
    setChangingControls(true);
    setNotice('');
    let sid = selected.current;
    try {
      if (!session) {
        if (sid === 'new') {
          sid = (await api.create(model)).session_id;
          selected.current = sid;
          writeDraft(draftRef.current.new ?? '', sid);
          writeDraft('', 'new');
        }
        const value = await api.session(sid);
        applySession(value);
      }
      const confirmed = 'active_tools' in change ? await api.selectTools(sid, change.active_tools) :
        'permission_mode' in change ? await api.selectPermissionMode(sid, change.permission_mode) :
        await api.selectThinking(sid, model, change.thinking_level);
      controlsRevision.current++;
      if (selected.current === sid) {
        setSession(previous => previous?.session_id === sid ? { ...previous, ...confirmed } : previous);
        setNotice('active_tools' in change ? '' : 'permission_mode' in change ?
          '权限模式已保存，下一批工具调用生效；当前审批保持原样。' : '推理强度已保存，下一次模型请求生效。');
      }
      try { await refreshSessions(); }
      catch { if (selected.current === sid) setNotice('选择已保存，会话列表刷新未确认。'); }
    } catch (error) {
      controlsRevision.current++;
      if (selected.current === sid) {
        setNotice(errorText(error));
        try {
          const actual = await api.session(sid);
          if (selected.current === sid) {
            controlsRevision.current++;
            if (!session) applySession(actual);
            else setSession(previous => previous?.session_id === sid ? {
              ...previous, active_tools: actual.active_tools, permission_mode: actual.permission_mode,
              model_id: actual.model_id, thinking_level: actual.thinking_level,
            } : previous);
            if ('thinking_level' in change) setModel(actual.model_id);
          }
        } catch { if (selected.current === sid) setNotice(errorText(error) + ' 当前选择刷新未确认，请重新打开会话核对。'); }
      }
    } finally { busySend.current = false; setChangingControls(false); }
  }

  async function submit(followUp: boolean) {
    if (busySend.current || selectionPending.current || !model) return;
    if (session?.recovery) { setNotice('当前仅可查看已保存历史。'); return; }
    const key = selected.current;
    const text = draftRef.current[key] ?? '';
    const queued = pendingRef.current[key] ?? [];
    const live = currentRun.current;
    if (stoppedMode.current[key] && live?.status === 'running') {
      if (text.trim()) { writePending([...queued, text], key); writeDraft('', key); }
      setNotice('上一轮正在停止，内容已保留待发送。');
      return;
    }
    const combined = stoppedMode.current[key] ? [...queued, text].filter(Boolean).join('\n\n') : text;
    if (!combined.trim()) return;
    if (combined.length > 32768) { setNotice('待发送内容过长，请编辑后分批提交。'); return; }
    busySend.current = true;
    setSubmitting(true);
    setNotice('');
    let sid = key;
    try {
      if (key === 'new') {
        sid = (await api.create(model)).session_id;
        selected.current = sid;
        writeDraft(draftRef.current.new ?? '', sid);
        writeDraft('', 'new');
        const value = await api.session(sid);
        setSession(value);
        history.open(sid, undefined, { ...value.history, messages: value.messages });
      }
      if (live?.status === 'running' && !live.done) {
        await api.control(live.run_id, followUp ? 'follow_up' : 'steering', combined);
        setLastSubmission({ session_id: sid });
        setNotice(followUp ? 'Follow-up 已排队，将在自然结束后处理。' : '补充消息已排队。');
      } else {
        const started = await api.start(sid, combined, undefined, session?.conversation_revision);
        setLastSubmission({ session_id: sid });
        const value = await api.run(started.run_id);
        currentRun.current = value;
        display(value, sid);
        void watch(started.run_id, sid, value);
        stoppedMode.current[sid] = false;
        writePending([], sid);
      }
      if (draftRef.current[sid] === text) writeDraft('', sid);
      await refreshSessions();
    } catch (error) { setNotice(errorText(error)); }
    finally { busySend.current = false; setSubmitting(false); }
  }

  async function regenerate() {
    if (busySend.current || !session?.can_run || !session.last_reply_id || selectionPending.current) return;
    if (!window.confirm('将重新执行最后一次任务。工作区文件不会回滚，工具可能再次执行。继续吗？')) return;
    const sid = session.session_id, generation = ++selectionGeneration.current;
    busySend.current = true; setSubmitting(true); subscription.current?.abort(); history.cancel();
    try {
      await api.regenerate(sid, session.last_reply_id, session.conversation_revision ?? 0);
      const value = await api.prepare(sid);
      if (generation === selectionGeneration.current && selected.current === sid) applySession(value, true);
      await refreshSessions();
    } catch (error) {
      if (selected.current === sid) {
        setNotice(errorText(error));
        try { const value = await api.prepare(sid); if (selected.current === sid) applySession(value, true); }
        catch { if (selected.current === sid) setNotice('重生成状态尚未确认，请重新选择会话核对。'); }
      }
    } finally { busySend.current = false; setSubmitting(false); }
  }

  async function compact() {
    if (busySend.current || selectionPending.current || changingModel) return;
    const sid = selected.current;
    if (!session || !session.can_run || session.busy || currentRun.current?.status === 'running') {
      setNotice('压缩需要已有会话处于空闲状态，并先处理待审批或恢复事项。'); return;
    }
    busySend.current = true;
    setSubmitting(true);
    setNotice('正在准备压缩上下文…');
    const original = draftRef.current[sid] ?? '';
    try {
      const started = await api.compact(sid);
      if (original.trim() === '/compact' && draftRef.current[sid] === original) writeDraft('', sid);
      const value = await api.run(started.run_id);
      display(value, sid);
      void watch(started.run_id, sid, value);
      await refreshSessions();
    } catch (error) { if (selected.current === sid) setNotice(errorText(error)); }
    finally { busySend.current = false; setSubmitting(false); }
  }

  async function stop() {
    const live = currentRun.current;
    const sid = selected.current;
    if (!live || live.status !== 'running' || stopping) return;
    setStopping(true);
    pendingStop.current = live.run_id;
    stoppedMode.current[sid] = true;
    try {
      const receipt = await api.control(live.run_id, 'stop');
      if (pendingStop.current === live.run_id) pendingStop.current = null;
      writePending([...receipt.retained, ...(pendingRef.current[sid] ?? [])], sid);
      if (selected.current === sid && currentRun.current?.run_id === live.run_id) {
        if (currentRun.current.status === 'running') display({ ...currentRun.current, stop_requested: true }, sid);
        setNotice(currentRun.current?.status === 'running'
        ? '已请求停止，正在等待执行和保存收尾。' : '停止请求已接受，保留内容可由你重新发送。');
      }
    } catch (error) {
      if (pendingStop.current === live.run_id) pendingStop.current = null;
      if (selected.current === sid && currentRun.current?.run_id === live.run_id) {
        setNotice(errorText(error)); setStopping(currentRun.current.stop_requested === true && currentRun.current.status === 'running');
      }
    }
  }

  async function approve(batch: ApprovalBatch, decisions: ToolDecision[], remember: string[] = []) {
    if (busySend.current || selectionPending.current || !session) return;
    const sid = session.session_id;
    busySend.current = true;
    setSubmitting(true);
    setNotice('');
    try {
      const started = await api.approve(sid, batch, decisions, remember);
      // A receipt retires only this batch; execution and other approvals may still be pending.
      approvalRevision.current++; setSession(current => {
        if (current?.session_id !== sid) return current;
        const batches = current.approval_batches?.filter(item => item.approval_batch_id !== batch.approval_batch_id) ?? [];
        return { ...current, approval_batches: batches, pending_approval: batches.length > 0 };
      });
      if (started.kind === 'live_child') {
        await refreshSession(sid);
        return;
      }
      const value = await api.run(started.run_id);
      display(value, sid);
      void watch(started.run_id, sid, value);
      stoppedMode.current[sid] = false;
    } catch (error) {
      if (selected.current === sid) setNotice(errorText(error));
      try {
        await refreshSession(sid);
      } catch {
        if (selected.current === sid) setNotice(errorText(error) + ' 当前审批刷新未确认，请重新同步后核对。');
      }
    } finally { busySend.current = false; setSubmitting(false); }
  }

  async function stopAgent(agentPath: string, originRunId: string, agentRunId?: string, approvalBatchId?: string) {
    const sid = selected.current;
    try {
      if (!await stopObservedSubagent(api, currentRun.current, agentPath, originRunId, agentRunId, approvalBatchId)) return;
      if (selected.current === sid) setNotice('已请求停止该子任务，其他子任务继续。');
      await refreshSession(sid);
    } catch (error) { if (selected.current === sid) setNotice(errorText(error)); }
  }

  async function stopApproval(batch: ApprovalBatch) {
    if (batch.kind === 'live_child') return stopAgent(batch.agent_path, batch.origin_run_id, undefined, batch.approval_batch_id);
    if (busySend.current || selectionPending.current || !session) return;
    const sid = session.session_id;
    busySend.current = true;
    setSubmitting(true);
    try {
      const started = await api.stopApproval(sid, batch);
      approvalRevision.current++; setSession(current => {
        if (current?.session_id !== sid) return current;
        const batches = current.approval_batches?.filter(item => item.approval_batch_id !== batch.approval_batch_id) ?? [];
        return { ...current, approval_batches: batches, pending_approval: batches.length > 0 };
      });
      const value = await api.run(started.run_id);
      display(value, sid);
      void watch(started.run_id, sid, value);
      stoppedMode.current[sid] = true;
    } catch (error) { if (selected.current === sid) setNotice(errorText(error)); }
    finally { busySend.current = false; setSubmitting(false); }
  }

  const id = selected.current;
  return { models, model, thinkingOptions, sessions, session, messages, run, notice, lastSubmission,
    connection: connection?.session_id === id && connection.run_id === run?.run_id ? connection : null, submitting, stopping, changingModel, changingControls, selectingSession, recovering,
    history: { ...history.pagination, loadOlder: history.loadOlder, disabled: selectingSession },
    managingSessions: listing.managingSessions, updateSession: listing.updateSession, reorderSessions: listing.reorderSessions, deleteSession,
    draft: drafts[id] ?? '', pending: pending[id] ?? [], pendingMode: !!stoppedMode.current[id],
    setDraft: writeDraft, setPending: writePending, submit, compact, regenerate, stop, approve, stopAgent, stopApproval, selectSession, newSession, selectModel,
    selectThinking: (level: string) => changeControls({ thinking_level: level }),
    selectTools: (names: string[]) => changeControls({ active_tools: names }),
    selectPermissionMode: (mode: PermissionMode) => changeControls({ permission_mode: mode }),
    recover: (action?: 'continue' | 'restore_approval') => {
      if (!selectionPending.current) return recover(session, action);
    },
    reconnect: () => { const live = currentRun.current; if (live) void watch(live.run_id, selected.current, undefined, true); },
  };
}
