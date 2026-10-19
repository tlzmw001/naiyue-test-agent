import type { HighlightPart, HighlightReply, HighlightRequest } from './code-highlight';

export const HIGHLIGHT_TIMEOUT_MS = 10_000;
interface Job extends HighlightRequest {
  signal: AbortSignal; finish: (parts?: HighlightPart[]) => void; cancel: () => void;
}
let worker: Worker | undefined;
let active: Job | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;
let nextId = 0;
let warnedUnavailable = false;
const queue: Job[] = [];

function releaseWorker() {
  worker?.terminate(); worker = undefined;
}
function settle(job: Job, parts?: HighlightPart[]) {
  job.signal.removeEventListener('abort', job.cancel);
  job.finish(parts);
}
function complete(parts?: HighlightPart[]) {
  if (timer) clearTimeout(timer);
  timer = undefined;
  const job = active; active = undefined;
  if (job) settle(job, parts);
  pump();
}
function fault(reason: string) {
  console.warn('code_highlight_degraded', { reason });
  releaseWorker(); complete();
}
function pump() {
  if (active) return;
  active = queue.shift();
  if (!active) { releaseWorker(); return; }
  if (typeof Worker === 'undefined') {
    if (!warnedUnavailable) { console.warn('code_highlight_degraded', { reason: 'worker_unavailable' }); warnedUnavailable = true; }
    complete(); return;
  }
  try {
    if (!worker) {
      worker = new Worker(new URL('./code-highlight.worker.ts', import.meta.url), { type: 'module' });
      const current = worker;
      worker.onmessage = (event: MessageEvent<HighlightReply>) => {
        if (worker !== current || event.data.id !== active?.id) return;
        const { parts, reason } = event.data;
        if (reason) console.warn('code_highlight_degraded', { reason });
        if (parts && parts.map(part => part.text).join('') !== active.code) { fault('text_mismatch'); return; }
        complete(parts);
      };
      worker.onerror = event => { event.preventDefault(); if (worker === current) fault('worker_error'); };
      worker.onmessageerror = () => { if (worker === current) fault('worker_message_error'); };
    }
    timer = setTimeout(() => fault('timeout'), HIGHLIGHT_TIMEOUT_MS);
    worker.postMessage({ id: active.id, code: active.code, language: active.language });
  } catch { fault('worker_start_failed'); }
}

// One worker for mounted code blocks; pending work is removed on body change/unmount.
export function requestHighlight(code: string, language: string, signal: AbortSignal): Promise<HighlightPart[] | undefined> {
  if (signal.aborted) return Promise.resolve(undefined);
  return new Promise(resolve => {
    const job: Job = { id: ++nextId, code, language, signal, finish: resolve, cancel() {
      if (active === job) { releaseWorker(); complete(); }
      else {
        const index = queue.indexOf(job);
        if (index !== -1) queue.splice(index, 1);
        settle(job);
      }
    } };
    signal.addEventListener('abort', job.cancel, { once: true });
    queue.push(job); pump();
  });
}
