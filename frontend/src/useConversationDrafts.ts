import { useRef, useState } from 'react';
import type { RefObject } from 'react';

/** Text remains keyed by session, including the uncreated "new" draft. */
export function useConversationDrafts(selected: RefObject<string>) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Record<string, string[]>>({});
  const draftRef = useRef<Record<string, string>>({});
  const pendingRef = useRef<Record<string, string[]>>({});
  function writeDraft(value: string, id = selected.current) {
    draftRef.current = { ...draftRef.current, [id]: value };
    setDrafts(draftRef.current);
  }
  function writePending(value: string[], id = selected.current) {
    pendingRef.current = { ...pendingRef.current, [id]: value };
    setPending(pendingRef.current);
  }
  function forget(id: string) {
    delete draftRef.current[id]; delete pendingRef.current[id];
    setDrafts({ ...draftRef.current }); setPending({ ...pendingRef.current });
  }
  return { drafts, pending, draftRef, pendingRef, writeDraft, writePending, forget };
}
