import { useRef, useState } from 'react';
import { ApiError, type WebApi } from './api';
import type { DraftControls, SessionView } from './types';

/** One new-session draft owns its original request until the result is known. */
export function useSessionCreation(api: WebApi) {
  const attempt = useRef<{ request: { model_id: string; requestId: string }; controls: DraftControls; sessionId?: string } | null>(null);
  const [controls, setControls] = useState<DraftControls>({});
  const controlDraft = useRef<DraftControls>({});
  const pendingControls = useRef(new Map<string, { model: string; controls: DraftControls }>());
  const [locked, setLocked] = useState(false), [preparing, setPreparing] = useState(false);
  function changeControls(value: DraftControls) {
    if (attempt.current) throw new ApiError('session_creation_unconfirmed');
    controlDraft.current = { ...controlDraft.current, ...value }; setControls(controlDraft.current);
  }
  function modelChanged() { changeControls({ thinking_level: undefined }); }
  function override(sid: string, change: DraftControls) {
    const pending = pendingControls.current.get(sid);
    if (pending) {
      Object.assign(pending.controls, change);
      for (const key of Object.keys(change) as (keyof DraftControls)[]) if (change[key] === undefined) delete pending.controls[key];
    }
  }
  function acknowledge(sid: string, change: DraftControls) {
    const pending = pendingControls.current.get(sid);
    if (pending) for (const key of Object.keys(change) as (keyof DraftControls)[]) delete pending.controls[key];
  }
  function hasPending(sid: string) { return !!Object.keys(pendingControls.current.get(sid)?.controls ?? {}).length; }
  async function configure(sid: string) {
    const pending = pendingControls.current.get(sid);
    if (!pending) return null;
    const confirmed: Partial<SessionView> = {};
    if (pending.controls.active_tools !== undefined) {
      Object.assign(confirmed, await api.selectTools(sid, pending.controls.active_tools)); delete pending.controls.active_tools;
    }
    if (pending.controls.permission_mode !== undefined) {
      Object.assign(confirmed, await api.selectPermissionMode(sid, pending.controls.permission_mode)); delete pending.controls.permission_mode;
    }
    if (pending.controls.thinking_level !== undefined) {
      Object.assign(confirmed, await api.selectThinking(sid, pending.model, pending.controls.thinking_level)); delete pending.controls.thinking_level;
    }
    pendingControls.current.delete(sid); return confirmed;
  }
  async function prepare(model: string, progress?: (view: SessionView) => void) {
    const current = attempt.current ??= { request: { model_id: model, requestId: crypto.randomUUID() },
      controls: { ...controlDraft.current, ...(controlDraft.current.active_tools ? { active_tools: [...controlDraft.current.active_tools] } : {}) } };
    setLocked(true); setPreparing(true);
    try {
      if (!current.sessionId) current.sessionId = (await api.create(current.request.model_id, current.request.requestId)).session_id;
      let view = await api.session(current.sessionId);
      pendingControls.current.set(view.session_id, { model: current.request.model_id, controls: current.controls });
      controlDraft.current = {}; setControls({});
      attempt.current = null; setLocked(false);
      progress?.(view);
      return view;
    } catch (error) {
      if (error instanceof ApiError && ['project_trust_required', 'project_execution_unavailable'].includes(error.code)) {
        attempt.current = null; setLocked(false);
      }
      throw error;
    } finally { setPreparing(false); }
  }
  return { locked, preparing, controls, changeControls, modelChanged, override, acknowledge, hasPending, prepare, configure };
}
