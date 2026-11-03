import { useRef, useState, type RefObject } from 'react';
import type { DraftImage } from './imageTypes';
import type { AnalysisDraft, MaterialRef } from './cloudReceiveTypes';

export interface ComposerDraft { text: string; images: DraftImage[]; materials: MaterialRef[] }
/** One authoritative input per session; material drafts persist only within this browser tab. */
export function useConversationDrafts(selected: RefObject<string>, projectId?: string) {
  const source = useRef<Record<string, ComposerDraft>>({});
  const [, repaint] = useState(0);
  const [pending, setPending] = useState<Record<string,string[]>>({});
  const draftRef = useRef<Record<string,string>>({});
  const imageRef = useRef<Record<string,DraftImage[]>>({});
  const materialRef = useRef<Record<string,MaterialRef[]>>({});
  const pendingRef = useRef<Record<string,string[]>>({});
  const storageKey = (id: string) => `naiyue.material-draft.${projectId}.${id}`;
  function input(id: string): ComposerDraft {
    if (source.current[id]) return source.current[id];
    let stored: { text?: string; materials?: MaterialRef[] } | null = null;
    if (projectId) try { stored = JSON.parse(sessionStorage.getItem(storageKey(id)) ?? 'null'); }
    catch { console.warn('cloud_material_draft_read_failed'); }
    const materials = Array.isArray(stored?.materials) ? stored.materials.filter(value => value && typeof value.material_id === 'string' &&
      typeof value.title === 'string' && typeof value.size === 'number' && typeof value.receive_id === 'string') : [];
    return source.current[id] = { text: materials.length && typeof stored?.text === 'string' ? stored.text : '', images: [], materials };
  }
  function project() {
    draftRef.current = Object.fromEntries(Object.entries(source.current).map(([id,input])=>[id,input.text]));
    imageRef.current = Object.fromEntries(Object.entries(source.current).map(([id,input])=>[id,input.images]));
    materialRef.current = Object.fromEntries(Object.entries(source.current).map(([id,input])=>[id,input.materials]));
  }
  input(selected.current); project();
  function publish(id: string) {
    project();
    if (projectId) try {
      const value = source.current[id];
      if (value?.materials.length) sessionStorage.setItem(storageKey(id), JSON.stringify({ text: value.text, materials: value.materials }));
      else sessionStorage.removeItem(storageKey(id));
    } catch { console.warn('cloud_material_draft_save_failed'); }
    repaint(value => value + 1);
  }
  function writeDraft(text: string, id = selected.current) {
    source.current[id] = { ...input(id), text }; publish(id);
  }
  function writeImages(images: DraftImage[], id = selected.current) {
    source.current[id] = { ...input(id), images }; publish(id);
  }
  function writeMaterials(materials: MaterialRef[], id = selected.current) {
    source.current[id] = { ...input(id), materials }; publish(id);
  }
  function mergeAnalysisDraft(value: AnalysisDraft) {
    const id = selected.current;
    if (!projectId || value.local_project_id !== projectId || value.local_session_id !== id || id === 'new') return false;
    const before = input(id), known = new Set(before.materials.map(item => item.material_id));
    const added = value.material_refs.filter(item => { if (known.has(item.material_id)) return false; known.add(item.material_id); return true; });
    if (!added.length) return true;
    source.current[id] = { ...before, text: [before.text, value.text].filter(Boolean).join('\n\n'), materials: [...before.materials, ...added] };
    publish(id); return true;
  }
  function appendDraft(text: string, images: DraftImage[], id = selected.current) {
    const before = input(id);
    source.current[id] = {...before,text:[before.text,text].filter(Boolean).join('\n\n'),images:[...before.images,...images]}; publish(id);
  }
  function moveDraft(from: string, to: string) {
    source.current[to] = input(from);
    delete source.current[from]; publish(from); publish(to);
  }
  function writePending(value: string[], id = selected.current) {
    pendingRef.current = { ...pendingRef.current, [id]: value }; setPending(pendingRef.current);
  }
  function forget(id: string) {
    delete source.current[id]; delete pendingRef.current[id];
    publish(id); setPending({ ...pendingRef.current });
  }
  return { drafts: draftRef.current, pending, draftRef, imageRef, materialRef, pendingRef, writeDraft, writeImages,
    writeMaterials, mergeAnalysisDraft, appendDraft, moveDraft, writePending, forget };
}
