import { useEffect, useRef, useState } from 'react';
import { ApiError, errorText, WebApi } from './api';
import { applyEvent, mergeMessages, validateEvent } from './events';
import type { MessageRow, RunView, SessionRow, SessionView, ToolDecision } from './types';

export function useConversation(api: WebApi) {
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [session, setSession] = useState<SessionView | null>(null);
  const [messages, setMessages] = useState<MessageRow[]>([]);
  const [run, setRun] = useState<RunView | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Record<string, string[]>>({});
  const [notice, setNotice] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [changingModel, setChangingModel] = useState(false);
  const [selectingSession, setSelectingSession] = useState(false);
  const selectionGeneration = useRef(0);
  const selectionPending = useRef(false);
  const selected = useRef<string>('new');
  const currentRun = useRef<RunView | null>(null);
  const draftRef = useRef<Record<string, string>>({});
  const pendingRef = useRef<Record<string, string[]>>({});
  const stoppedMode = useRef<Record<string, boolean>>({});
  const busySend = useRef(false);
  const subscription = useRef<AbortController | null>(null);

  const writeDraft = (value: string, id = selected.current) => {
    draftRef.current = { ...draftRef.current, [id]: value };
    setDrafts(draftRef.current);
  };
  const writePending = (value: string[], id = selected.current) => {
    pendingRef.current = { ...pendingRef.current, [id]: value };
    setPending(pendingRef.current);
  };

  async function refreshSessions() { setSessions(await api.sessions()); }

  useEffect(() => {
    let disposed = false;
    Promise.all([api.models(), api.sessions()]).then(([config, rows]) => {
      if (!disposed) { setModels(config.model_ids); setModel(config.default_model_id); setSessions(rows); }
    }).catch(error => { if (!disposed) setNotice(errorText(error)); });
    return () => { disposed = true; subscription.current?.abort(); };
  }, [api]);

  function display(view: RunView, sid: string, replace = false) {
    if (selected.current !== sid) return;
    currentRun.current = view;
    setRun(view);
    setMessages(previous => replace ? view.messages : mergeMessages(previous, view.messages));
    if (view.status !== 'running') {
      setStopping(false);
      if (view.status === 'stopped') setNotice('已停止，保留内容可由你重新发送。');
    }
  }

  async function watch(runId: string, sid: string, initial?: RunView) {
    if (selected.current !== sid) return;
    subscription.current?.abort();
    const controller = new AbortController();
    subscription.current = controller;
    let view: RunView;
    try {
      view = initial ?? await api.run(runId);
      display(view, sid);
      if (view.done) {
        const refreshed = await api.session(sid);
        if (selected.current === sid) setSession(refreshed);
        await refreshSessions();
        return;
      }
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          await api.events(runId, view.last_sequence, controller.signal, frame => {
            if (frame.event === 'resync_required') throw new ApiError('resync_required');
            if (frame.event !== 'runtime') return;
            const event = validateEvent(frame.data);
            if (event.run_id !== runId || event.session_id !== sid) throw new Error('invalid_event');
            view = applyEvent(view, event);
            display(view, sid);
            if (event.event_type === 'control_delivered' && selected.current === sid)
              setNotice('补充消息已加入本次上下文。');
          });
          if (!controller.signal.aborted && view.status === 'running') throw new Error('stream_truncated');
          break;
        } catch (error) {
          if (controller.signal.aborted) return;
          if (attempt === 1) throw error;
          view = await api.run(runId);
          display(view, sid);
          if (view.done) break;
        }
      }
      if (!controller.signal.aborted) {
        const confirmed = await api.run(runId);
        display(confirmed, sid);
        const refreshed = await api.session(sid);
        if (selected.current === sid) setSession(refreshed);
        await refreshSessions();
      }
    } catch (error) {
      if (!controller.signal.aborted && selected.current === sid) setNotice(errorText(error));
    }
  }

  async function selectSession(id: string) {
    if (busySend.current) return;
    subscription.current?.abort();
    const generation = ++selectionGeneration.current;
    selectionPending.current = true;
    setSelectingSession(true);
    setNotice('');
    try {
      const value = await api.session(id);
      if (generation !== selectionGeneration.current) return;
      selected.current = id;
      setSession(value);
      setModel(value.model_id);
      setMessages(value.messages);
      currentRun.current = value.live;
      setRun(value.live);
      setStopping(false);
      if (value.live?.status === 'stopped') {
        stoppedMode.current[id] = true;
        if (!(id in pendingRef.current)) writePending(value.live.retained ?? [], id);
      }
      if (value.live && !value.live.done) void watch(value.live.run_id, id, value.live);
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

  async function selectModel(value: string) {
    if (changingModel || selectionPending.current) return;
    if (!session) { setModel(value); return; }
    const sid = session.session_id;
    setChangingModel(true);
    try {
      await api.select(sid, value);
      if (selected.current !== sid) { await refreshSessions(); return; }
      setModel(value);
      setSession(previous => previous ? { ...previous, model_id: value } : previous);
      await refreshSessions();
      setNotice('模型已切换，下一次模型调用生效。');
    } catch (error) { if (selected.current === sid) setNotice(errorText(error)); }
    finally { setChangingModel(false); }
  }

  async function submit(followUp: boolean) {
    if (busySend.current || selectionPending.current || !model) return;
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
      }
      if (live?.status === 'running' && !live.done) {
        await api.control(live.run_id, followUp ? 'follow_up' : 'steering', combined);
        setNotice(followUp ? 'Follow-up 已排队，将在自然结束后处理。' : '补充消息已排队。');
      } else {
        const started = await api.start(sid, combined);
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

  async function stop() {
    const live = currentRun.current;
    const sid = selected.current;
    if (!live || live.status !== 'running' || stopping) return;
    setStopping(true);
    stoppedMode.current[sid] = true;
    try {
      const receipt = await api.control(live.run_id, 'stop');
      writePending([...receipt.retained, ...(pendingRef.current[sid] ?? [])], sid);
      if (selected.current === sid) setNotice(currentRun.current?.status === 'running'
        ? '已请求停止，正在等待执行和保存收尾。' : '停止请求已接受，保留内容可由你重新发送。');
    } catch (error) {
      if (selected.current === sid) { setNotice(errorText(error)); setStopping(false); }
    }
  }

  async function approve(decisions: ToolDecision[]) {
    if (busySend.current || selectionPending.current || !session?.approval_run_id) return;
    const sid = session.session_id;
    busySend.current = true;
    setSubmitting(true);
    setNotice('');
    try {
      const started = await api.approve(sid, session.approval_run_id, decisions);
      const value = await api.run(started.run_id);
      display(value, sid);
      void watch(started.run_id, sid, value);
      stoppedMode.current[sid] = false;
    } catch (error) {
      if (selected.current === sid) setNotice(errorText(error));
    } finally { busySend.current = false; setSubmitting(false); }
  }

  const id = session?.session_id ?? 'new';
  return { models, model, sessions, session, messages, run, notice, submitting, stopping, changingModel, selectingSession,
    draft: drafts[id] ?? '', pending: pending[id] ?? [], pendingMode: !!stoppedMode.current[id],
    setDraft: writeDraft, setPending: writePending, submit, stop, approve, selectSession, newSession, selectModel,
    reconnect: () => { const live = currentRun.current; if (live) void watch(live.run_id, selected.current); },
  };
}
