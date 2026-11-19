/** Read discovery only: opening/recovering/executing remains an explicit action. */
import { useEffect, useRef, useState } from 'react';
import { ApiError, errorText, type WebApi } from './api';
import type { SessionView } from './types';
import { sessionSyncPollMs } from './uiSettings';

type Options = { api: WebApi; active: boolean; session: SessionView | null; canRefresh: (resuming?: boolean) => boolean; revision: () => string;
  applySession: (value: SessionView) => void; refreshSessions: (signal?: AbortSignal) => Promise<void>;
  onModels: (value: Awaited<ReturnType<WebApi['models']>>) => void;
  onError: (error: unknown) => void; onClose: () => void };
function facts(value: SessionView) {
  return JSON.stringify([value.conversation_revision, value.busy, value.can_run, value.model_id,
    value.thinking_level, value.permission_mode, value.active_tools, value.live?.run_id,
    value.live?.last_sequence, value.live?.done, value.pending_approval, value.execution_root_error]);
}
export function useConversationSync(options: Options) {
  const [failure,setFailure]=useState<{sessionId:string;message:string}|null>(null);
  const latest = useRef(options); latest.current = options;
  const resume = useRef(false);
  const modelsRevision = useRef(0);
  const refreshing = useRef(false);
  const [modelRefresh, setModelRefresh] = useState({ pending: false, message: '', failed: false });

  async function refreshModels() {
    if (refreshing.current) return;
    refreshing.current = true;
    setModelRefresh({ pending: true, message: '', failed: false });
    try {
      await options.api.refreshModels();
      window.dispatchEvent(new Event('naiyue:models-refreshed'));
      setModelRefresh({ pending: false, message: '模型配置已刷新。', failed: false });
      void latest.current.refreshSessions().catch(error => latest.current.onError(error));
    } catch (error) {
      const message = error instanceof ApiError && error.code === 'model_config_refresh_failed'
        ? '无法刷新模型配置，请检查配置文件后重试。'
        : error instanceof ApiError && error.code === 'model_config_refresh_busy'
          ? '当前有会话或后台记忆任务正在运行，请结束后刷新。' : errorText(error);
      setModelRefresh({ pending: false, message, failed: true });
    } finally { refreshing.current = false; }
  }
  const api = options.api, id = options.session?.session_id, active = options.active;
  useEffect(() => {
    let disposed = false;
    const closed = latest.current.onClose;
    void latest.current.refreshSessions().catch(error => { if (!disposed) latest.current.onError(error); });
    return () => { disposed = true; closed(); };
  }, [api]);
  useEffect(() => {
    if (!active) return;
    let disposed = false;
    function readModels() {
      const revision = ++modelsRevision.current;
      void api.models().then(config => {
        if (!disposed && revision === modelsRevision.current) latest.current.onModels(config);
      }).catch(error => { if (!disposed && revision === modelsRevision.current) latest.current.onError(error); });
    }
    readModels();
    window.addEventListener('naiyue:models-refreshed', readModels);
    return () => { disposed = true; window.removeEventListener('naiyue:models-refreshed', readModels); };
  }, [api, active]);
  useEffect(() => {
    if (!active) { resume.current = true; latest.current.onClose(); return; }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      let permanent = false;
      let readingStatus = false;
      try {
        if (document.visibilityState === 'visible') {
          const revision = latest.current.revision();
          if (id && latest.current.canRefresh(resume.current)) {
            readingStatus = true;
            const value = await api.session(id, controller.signal);
            readingStatus = false;
            if(!controller.signal.aborted)setFailure(null);
            const current = latest.current.session;
            if (!controller.signal.aborted && current?.session_id === id && latest.current.canRefresh(resume.current) &&
                revision === latest.current.revision() &&
                (value.conversation_revision ?? 0) >= (current.conversation_revision ?? 0) && (resume.current || facts(value) !== facts(current))) {
              latest.current.applySession(value);
              resume.current = false;
            }
          }
          if (!controller.signal.aborted) await latest.current.refreshSessions(controller.signal);
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          if(readingStatus&&id){
            setFailure({sessionId:id,message:errorText(error)});
            console.warn('session_status_sync_failed',error instanceof ApiError?error.code:error instanceof Error?error.name:'unknown');
          }else latest.current.onError(error);
        }
        permanent = error instanceof ApiError && ['unauthorized', 'invalid_host', 'invalid_origin',
          'service_instance_mismatch', 'session_not_found'].includes(error.code);
      } finally {
        if (!controller.signal.aborted && !permanent) timer = setTimeout(tick, sessionSyncPollMs);
      }
    };
    if (resume.current) void tick();
    else timer = setTimeout(tick, sessionSyncPollMs);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, id, active]);
  return { refreshModels, modelRefresh, error:failure&&failure.sessionId===id?failure.message:'',
    clear:(sessionId:string)=>setFailure(current=>current?.sessionId===sessionId?null:current)};
}
