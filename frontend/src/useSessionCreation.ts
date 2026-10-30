import { useRef, useState } from 'react';
import { ApiError, type WebApi } from './api';
import type { DraftControls, ExecutionDraft, SessionCreation, SessionView } from './types';

/** One new-session draft owns its original request until the result is known. */
export function useSessionCreation(api: WebApi) {
  const [draft, setDraft] = useState<ExecutionDraft | null>(null);
  const draftRef = useRef<ExecutionDraft | null>(null);
  const attempt = useRef<{ request: SessionCreation; trustRevision?: number; trustId: string; trusted: boolean;
    controls: DraftControls; sessionId?: string } | null>(null);
  const [controls, setControls] = useState<DraftControls>({});
  const controlDraft = useRef<DraftControls>({});
  const pendingControls = useRef(new Map<string, { model: string; controls: DraftControls }>());
  const [locked, setLocked] = useState(false), [preparing, setPreparing] = useState(false);
  const review = useRef<string | null>(null);
  function choose(value?: ExecutionDraft) {
    if (value === undefined) return;
    if (attempt.current) throw new ApiError('env_operation_unknown');
    draftRef.current = value; setDraft(value); review.current = null;
  }
  function changeControls(value: DraftControls) {
    if (attempt.current) throw new ApiError('env_operation_unknown');
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
    if (review.current) throw new ApiError(review.current);
    const selected = draftRef.current;
    const current = attempt.current ??= { request: { execution: selected?.execution ?? 'host', model_id: model,
      include_untracked: [...(selected?.include_untracked ?? [])], profile_id: selected?.profile_id,
      expected_profile_revision: selected?.expected_profile_revision, requestId: crypto.randomUUID() },
      trustRevision: selected?.trust_revision, trustId: crypto.randomUUID(), trusted: false,
      controls: { ...controlDraft.current, ...(controlDraft.current.active_tools ? { active_tools: [...controlDraft.current.active_tools] } : {}) } };
    setLocked(true); setPreparing(true);
    let creating = false;
    try {
      if (current.trustRevision !== undefined && !current.trusted) {
        await api.trustGit(current.trustRevision, current.trustId); current.trusted = true;
      }
      creating = true;
      if (!current.sessionId) current.sessionId = (await api.createSelected(current.request.model_id, current.request)).session_id;
      let view = await api.session(current.sessionId);
      pendingControls.current.set(view.session_id, { model: current.request.model_id, controls: current.controls });
      controlDraft.current = {}; setControls({});
      attempt.current = null; draftRef.current = null; setDraft(null); setLocked(false);
      progress?.(view);
      return view;
    } catch (error) {
      if (error instanceof ApiError && (['env_profile_changed', 'env_profile_unavailable', 'env_image_required',
        'env_profile_selection_invalid', 'project_trust_required', 'project_execution_unavailable'].includes(error.code) ||
        !creating && ['project_location_changed', 'project_invalid_source'].includes(error.code))) {
        attempt.current = null; setLocked(false); review.current = error.code;
      }
      throw error;
    } finally { setPreparing(false); }
  }
  return { draft, locked, preparing, controls, choose, changeControls, modelChanged, override, acknowledge, hasPending, prepare, configure };
}
