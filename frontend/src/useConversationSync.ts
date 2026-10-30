/** Read discovery only: opening/recovering/executing remains an explicit action. */
import { useEffect, useRef } from 'react';
import { ApiError, type WebApi } from './api';
import type { SessionView } from './types';
import { sessionSyncPollMs } from './uiSettings';

type Options = { api: WebApi; active: boolean; session: SessionView | null; canRefresh: (resuming?: boolean) => boolean; revision: () => string;
  applySession: (value: SessionView) => void; refreshSessions: (signal?: AbortSignal) => Promise<void>;
  onModels: (value: Awaited<ReturnType<WebApi['models']>>) => void;
  onError: (error: unknown) => void; onClose: () => void };
function facts(value: SessionView) {
  return JSON.stringify([value.conversation_revision, value.busy, value.can_run, value.model_id,
    value.thinking_level, value.permission_mode, value.active_tools, value.live?.run_id,
    value.live?.last_sequence, value.live?.done, value.pending_approval, value.execution_root_error,value.execution,value.pending_submission]);
}
export function useConversationSync(options: Options) {
  const latest = useRef(options); latest.current = options;
  const resume = useRef(false);
  const api = options.api, id = options.session?.session_id, active = options.active;
  useEffect(() => {
    let disposed = false;
    const closed = latest.current.onClose;
    Promise.all([api.models(), latest.current.refreshSessions()]).then(([config]) => {
      if (!disposed) latest.current.onModels(config);
    }).catch(error => { if (!disposed) latest.current.onError(error); });
    return () => { disposed = true; closed(); };
  }, [api]);
  useEffect(() => {
    if (!active) { resume.current = true; latest.current.onClose(); return; }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      let permanent = false;
      try {
        if (document.visibilityState === 'visible') {
          const revision = latest.current.revision();
          if (id && latest.current.canRefresh(resume.current)) {
            const value = await api.session(id, controller.signal);
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
        if (!controller.signal.aborted) latest.current.onError(error);
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
}
