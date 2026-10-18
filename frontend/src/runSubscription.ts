import { ApiError, type WebApi } from './api';
import { applyEvent, validateEvent } from './events';
import type { RunView, RuntimeEvent } from './types';
import { runReconnectDelays } from './uiSettings';

function pause(milliseconds: number, signal: AbortSignal) {
  if (signal.aborted) return Promise.resolve();
  return new Promise<void>(resolve => {
    const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve(); };
    const timer = setTimeout(finish, milliseconds);
    signal.addEventListener('abort', finish, { once: true });
  });
}

export async function subscribeRun({ api, runId, sessionId, signal, initial, resynchronize = false,
  synchronize, update, reconnecting }: {
  api: WebApi; runId: string; sessionId: string; signal: AbortSignal; initial?: RunView; resynchronize?: boolean;
  synchronize: () => Promise<unknown>; update: (view: RunView, event?: RuntimeEvent) => void;
  reconnecting: (attempt: number) => void;
}) {
  const check = (view: RunView) => {
    if (view.run_id !== runId || view.session_id !== sessionId) throw new Error('invalid_event');
    update(view);
  };
  for (let attempt = 0; attempt <= runReconnectDelays.length; attempt++) {
    if (signal.aborted) return;
    try {
      if (resynchronize || attempt > 0) await synchronize();
      if (signal.aborted) return;
      let view = attempt === 0 && initial ? initial : await api.run(runId, signal);
      if (signal.aborted) return;
      check(view);
      if (view.done) return view;
      await api.events(runId, view.last_sequence, signal, frame => {
        if (signal.aborted) return;
        if (frame.event === 'resync_required') throw new ApiError('resync_required');
        if (frame.event !== 'runtime') return;
        const event = validateEvent(frame.data);
        if (event.run_id !== runId || event.session_id !== sessionId) throw new Error('invalid_event');
        view = applyEvent(view, event);
        update(view, event);
      });
      if (signal.aborted) return;
      if (view.status === 'running') throw new Error('stream_truncated');
      const confirmed = await api.run(runId, signal);
      if (signal.aborted) return;
      check(confirmed);
      return confirmed;
    } catch (error) {
      if (signal.aborted) return;
      const permanent = error instanceof ApiError && ['unauthorized', 'invalid_host', 'invalid_origin', 'run_not_found', 'session_not_found'].includes(error.code) ||
        error instanceof Error && error.message === 'invalid_event';
      if (permanent || attempt === runReconnectDelays.length) throw error;
      reconnecting(attempt + 1);
      await pause(runReconnectDelays[attempt], signal);
    }
  }
}
