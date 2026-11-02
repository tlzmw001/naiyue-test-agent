import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from './cloudApi';
import { cloudError } from './cloudPresentation';

export function useCloudDraft(api: CloudApi, sid: string, bid: string) {
  const key = `naiyue.cloud-draft.${api.projectId}.${sid}.${bid}`;
  const value = useRef({ text: '', model_id: null as string | null, thinking_level: 'model_default', base: 0, version: 0 });
  const alive = useRef(true); const lock = useRef(false);
  const [state, setState] = useState(value.current); const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false); const [error, setError] = useState(''); const [conflict, setConflict] = useState(false);
  function remember() {
    try { sessionStorage.setItem(key, JSON.stringify(value.current)); }
    catch { console.warn('cloud_draft_cache_failed'); if (alive.current) setError('浏览器草稿缓存不可用，请及时保存'); }
  }
  function change(next: Partial<typeof value.current>) {
    value.current = { ...value.current, ...next, version: value.current.version + 1 }; remember(); setState(value.current);
  }
  useEffect(() => {
    alive.current = true; const controller = new AbortController();
    void load(false, controller.signal);
    return () => { alive.current = false; controller.abort(); };
  }, [api, sid, bid]);
  async function load(discard: boolean, signal?: AbortSignal) {
    setReady(false);
    try {
      const saved = await api.draft(sid, bid, signal); if (signal?.aborted) return;
      let cached: typeof value.current | undefined;
      try { cached = discard ? undefined : JSON.parse(sessionStorage.getItem(key) ?? 'null') ?? undefined; }
      catch { console.warn('cloud_draft_cache_invalid'); }
      if (cached && (typeof cached.text !== 'string' || typeof cached.base !== 'number' || typeof cached.version !== 'number' || cached.model_id !== null && typeof cached.model_id !== 'string' || cached.thinking_level !== undefined && typeof cached.thinking_level !== 'string')) cached = undefined;
      setConflict(!!cached && cached.base !== saved.draft_revision && (cached.text !== saved.text || cached.model_id !== saved.model_id || (cached.thinking_level ?? 'model_default') !== (saved.thinking_level ?? 'model_default')));
      value.current = { text: cached?.text ?? saved.text, model_id: cached?.model_id ?? saved.model_id, thinking_level: cached?.thinking_level ?? saved.thinking_level ?? 'model_default', base: saved.draft_revision, version: cached?.version ?? 0 };
      setState(value.current); setReady(true); setError(''); remember();
    } catch (failure) { if (!signal?.aborted) { console.warn('cloud_draft_read_failed'); setError(cloudError(failure)); } }
  }
  async function save() {
    if (!ready || conflict || lock.current) throw new Error('cloud_draft_not_ready');
    lock.current = true; setSaving(true); const current = { ...value.current };
    try {
      const result = await api.saveDraft(sid, bid, { text: current.text, model_id: current.model_id, thinking_level: current.thinking_level, expected_draft_revision: current.base });
      value.current.base = result.draft_revision; remember();
      if (alive.current) setError('');
      return { ...current, draft_revision: result.draft_revision };
    } catch (failure) { console.warn('cloud_draft_save_failed'); if (alive.current) { setError(cloudError(failure)); setConflict(true); } throw failure; }
    finally { lock.current = false; if (alive.current) setSaving(false); }
  }
  async function acknowledged(version: number) {
    if (value.current.version === version) { value.current = { ...value.current, text: '' }; remember(); if (alive.current) setState(value.current); }
    try {
      const saved = await api.draft(sid, bid); value.current.base = saved.draft_revision; remember();
      if (alive.current && saved.text && (saved.text !== value.current.text || saved.model_id !== value.current.model_id || (saved.thinking_level ?? 'model_default') !== value.current.thinking_level)) setConflict(true);
    } catch { console.warn('cloud_draft_ack_read_failed'); }
  }
  return { ...state, ready, saving, error, conflict, save, acknowledged,
    setText: (text: string) => change({ text }), setModel: (model_id: string) => change({ model_id, thinking_level: 'model_default' }),
    setThinking: (thinking_level: string) => change({ thinking_level }), reload: () => void load(true) };
}
