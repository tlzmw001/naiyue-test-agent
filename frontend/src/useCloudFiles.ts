import { useEffect, useState } from 'react';
import type { CloudApi } from './cloudApi';
import type { CloudAttachment, CloudQueuedMessage } from './cloudTypes';
import { backgroundTaskPollMs } from './uiSettings';
import { cloudError } from './cloudPresentation';

export function useCloudFiles(api: CloudApi, sid: string, bid: string, active: boolean) {
  const [attachments, setAttachments] = useState<CloudAttachment[]>([]);
  const [queue, setQueue] = useState<CloudQueuedMessage | null>(null);
  const [ready, setReady] = useState(false); const [error, setError] = useState('');
  const [version, refresh] = useState(0);
  useEffect(() => {
    if (!active) return;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const [files, pending] = await Promise.all([api.attachments(sid, bid, abort.signal), api.queuedMessage(sid, bid, abort.signal)]);
        if (!abort.signal.aborted) { setAttachments(files); setQueue(pending); setReady(true); setError(''); }
      } catch (failure) { if (!abort.signal.aborted) { setReady(false); setError(cloudError(failure)); } }
      if (!abort.signal.aborted) timer = setTimeout(() => void load(), backgroundTaskPollMs);
    }
    void load(); return () => { abort.abort(); clearTimeout(timer); };
  }, [api, sid, bid, active, version]);
  return { attachments, queue, ready, error, refresh: () => refresh(v => v + 1) };
}
