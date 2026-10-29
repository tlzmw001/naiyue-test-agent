import type { RefObject } from 'react';
import type { WebApi } from './api';
import { ApiError, errorText } from './api';
import type { SessionView, RunView } from './types';
import type { useSessionCreation } from './useSessionCreation';
import type { useComposerImages } from './useComposerImages';
import { wireImages, type FrozenImageRequest } from './imageTypes';

type Context = {
  busySend: RefObject<boolean>; selectionPending: RefObject<boolean>; model: string; session: SessionView | null;
  selected: RefObject<string>; draftRef: RefObject<Record<string,string>>; pendingRef: RefObject<Record<string,string[]>>;
  currentRun: RefObject<RunView | null>; stoppedMode: RefObject<Record<string,boolean>>;
  writeDraft: (value:string,id?:string)=>void; writePending: (value:string[],id?:string)=>void;
  setNotice: (value:string)=>void; setSubmitting:(value:boolean)=>void;
  setLastSubmission:(value:{session_id:string})=>void;
  setSession: (value:(previous:SessionView|null)=>SessionView|null)=>void;
  creation: ReturnType<typeof useSessionCreation>; imageInputs: ReturnType<typeof useComposerImages>; api: WebApi;
  applySession:(value:SessionView, selected?:boolean)=>void;
  display:(value:RunView,id:string)=>void; watch:(run:string,id:string,initial?:RunView)=>Promise<void>;
  refreshSessions:()=>Promise<void>; refreshSession:(id:string)=>Promise<SessionView>;
};
export function createConversationSubmit(context:Context) {
  const { busySend,selectionPending,model,session,selected,draftRef,pendingRef,currentRun,stoppedMode,
    writeDraft,writePending,setNotice,setSubmitting,setLastSubmission,setSession,creation,imageInputs,api,
    applySession,display,watch,refreshSessions,refreshSession } = context;
  return async function submit(followUp: boolean, retryUncertain = false) {
    if (busySend.current || selectionPending.current || !model) return;
    if (session?.recovery) { setNotice('当前仅可查看已保存历史。'); return; }
    const key = selected.current;
    const frozen = retryUncertain ? imageInputs.uncertain : undefined;
    if (imageInputs.uncertain && !retryUncertain) { setNotice('上次图片发送结果尚未确认，请先核对原请求。新输入已保留。'); return; }
    if (retryUncertain && !frozen) return;
    const text = frozen?.draftText ?? draftRef.current[key] ?? '';
    const queued = pendingRef.current[key] ?? [];
    const attached = frozen?.images ?? imageInputs.ref.current[key] ?? [];
    if (imageInputs.reading) return;
    if (attached.length && !frozen && !imageInputs.supports(model)) { setNotice('当前模型不支持或尚未确认支持图片，请切换模型或移除图片。'); return; }
    const live = currentRun.current;
    if (!frozen && stoppedMode.current[key] && live?.status === 'running') {
      if (attached.length) { setNotice('上一轮正在停止，图片已保留在输入框。'); return; }
      if (text.trim()) { writePending([...queued, text], key); writeDraft('', key); }
      setNotice('上一轮正在停止，内容已保留待发送。');
      return;
    }
    const combined = frozen ? frozen.text : stoppedMode.current[key] ? [...queued, text].filter(Boolean).join('\n\n') : text;
    if (!combined.trim() && !attached.length) return;
    if (combined.length > 32768) { setNotice('待发送内容过长，请编辑后分批提交。'); return; }
    busySend.current = true;
    setSubmitting(true);
    setNotice('');
    let sid = key;
    let request: FrozenImageRequest | undefined = frozen;
    try {
      if (key === 'new') {
        const value = await creation.prepare(model, progress => {
          if (sid === 'new') {
            sid = progress.session_id; imageInputs.move('new', sid); applySession(progress, true);
          } else if (selected.current === sid) setSession(() => progress);
        });
        sid = value.session_id;
        applySession(value);
        if (value.execution?.kind === 'sandbox' && (value.execution.state !== 'ready' || !value.can_run)) {
          setNotice('环境准备尚未完成，消息已保留。请在执行位置菜单核对原准备。'); await refreshSessions(); return;
        }
      }
      const configured = await creation.configure(sid);
      if (configured) setSession(previous => previous?.session_id === sid ? { ...previous, ...configured } : previous);
      if (attached.length && !request) request = imageInputs.freeze({ requestId: crypto.randomUUID(), sessionId: sid,
        kind: live?.status === 'running' && !live.done ? followUp ? 'follow_up' : 'steering' : 'message',
        runId: live?.status === 'running' && !live.done ? live.run_id : undefined,
        text: combined, draftText: text, images: attached, revision: session?.conversation_revision });
      if (request ? request.kind !== 'message' : live?.status === 'running' && !live.done) {
        if (request && request.kind !== 'message') await api.control(request.runId!, request.kind, request.text, 'root', undefined, undefined, wireImages(request.images), [...new Set(request.images.flatMap(image => image.pendingId ? [image.pendingId] : []))], request.requestId);
        else await api.control(live!.run_id, followUp ? 'follow_up' : 'steering', combined);
        setLastSubmission({ session_id: sid });
        setNotice(followUp ? 'Follow-up 已排队，将在自然结束后处理。' : '补充消息已排队。');
      } else {
        const started = attached.length ? await api.start(sid, combined, request!.requestId, request!.revision, wireImages(attached), [...new Set(attached.flatMap(image => image.pendingId ? [image.pendingId] : []))]) : await api.start(sid, combined, undefined, session?.conversation_revision);
        setLastSubmission({ session_id: sid });
        const value = await api.run(started.run_id);
        currentRun.current = value;
        display(value, sid);
        void watch(started.run_id, sid, value);
        stoppedMode.current[sid] = false;
        writePending([], sid);
      }
      if (request) imageInputs.settle(sid,request.requestId);
      const sent = new Set(attached.map(image => image.client_id));
      imageInputs.write((imageInputs.ref.current[sid] ?? []).filter(image => !sent.has(image.client_id)), sid);
      void imageInputs.refreshPending();
      if (draftRef.current[sid] === text) writeDraft('', sid);
      await refreshSessions();
    } catch (error) {
      if (request && error instanceof ApiError && (['invalid_request','image_input_invalid','image_input_format_unsupported','image_input_too_large','image_input_pixel_limit','model_image_input_unsupported','image_input_busy','pending_image_limit','pending_image_invalid','session_busy','stale_run','run_capacity_exceeded','request_too_large'].includes(error.code))) imageInputs.settle(sid,request.requestId);
      console.warn('conversation_submit_failed', error instanceof ApiError ? error.code : error instanceof Error ? error.name : 'unknown');
      setNotice(errorText(error) + (creation.hasPending(sid) ? ' 设置尚未确认，再次发送将继续保存所选设置。' : ''));
      if (sid !== 'new') try { await refreshSession(sid); } catch { console.warn('submitted_session_refresh_failed'); }
    }
    finally { busySend.current = false; setSubmitting(false); }
  }

}
