import { useRef, useState, type RefObject } from 'react';
import type { DraftImage } from './imageTypes';

export interface ComposerDraft { text: string; images: DraftImage[] }
/** One authoritative input per session. Text/image maps are read projections. */
export function useConversationDrafts(selected: RefObject<string>) {
  const source = useRef<Record<string, ComposerDraft>>({});
  const [drafts, setDrafts] = useState<Record<string,string>>({});
  const [pending, setPending] = useState<Record<string,string[]>>({});
  const draftRef = useRef<Record<string,string>>({});
  const imageRef = useRef<Record<string,DraftImage[]>>({});
  const pendingRef = useRef<Record<string,string[]>>({});
  function publish() {
    draftRef.current = Object.fromEntries(Object.entries(source.current).map(([id,input])=>[id,input.text]));
    imageRef.current = Object.fromEntries(Object.entries(source.current).map(([id,input])=>[id,input.images]));
    setDrafts(draftRef.current);
  }
  function writeDraft(text: string, id = selected.current) {
    source.current[id] = { text, images: source.current[id]?.images ?? [] }; publish();
  }
  function writeImages(images: DraftImage[], id = selected.current) {
    source.current[id] = { images, text: source.current[id]?.text ?? '' }; publish();
  }
  function appendDraft(text: string, images: DraftImage[], id = selected.current) {
    const before = source.current[id] ?? {text:'',images:[]};
    source.current[id] = {text:[before.text,text].filter(Boolean).join('\n\n'),images:[...before.images,...images]}; publish();
  }
  function moveDraft(from: string, to: string) {
    source.current[to] = source.current[from] ?? { text:'', images:[] };
    delete source.current[from]; publish();
  }
  function writePending(value: string[], id = selected.current) {
    pendingRef.current = { ...pendingRef.current, [id]: value }; setPending(pendingRef.current);
  }
  function forget(id: string) {
    delete source.current[id]; delete pendingRef.current[id];
    publish(); setPending({ ...pendingRef.current });
  }
  return { drafts, pending, draftRef, imageRef, pendingRef, writeDraft, writeImages, appendDraft, moveDraft, writePending, forget };
}
