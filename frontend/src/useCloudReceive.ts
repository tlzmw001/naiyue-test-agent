import { useEffect, useRef, useState } from 'react';
import type { CloudApi } from './cloudApi';
import type { ReceiveRecord } from './cloudReceiveTypes';
import { cloudError } from './cloudPresentation';
import { backgroundTaskPollMs } from './uiSettings';

type Intent = { command_id: string; artifact_ids: string[]; include_code: boolean };
export function useCloudReceive(api: CloudApi, sid: string, bid: string, rid: string, inputId: string) {
  const key = `naiyue.cloud-materials.${api.projectId}.${sid}.${bid}.${rid}`;
  const alive = useRef(true); const flight = useRef(false); const controller = useRef<AbortController | undefined>(undefined);
  const [record, setRecord] = useState<ReceiveRecord>(); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  useEffect(() => { alive.current = true; return () => { alive.current = false; controller.current?.abort(); }; }, [key]);
  function checked(value: ReceiveRecord, request: Intent) {
    if (value.receive_id !== request.command_id || value.command_id !== request.command_id || value.local_project_id !== api.projectId ||
      value.local_session_id !== sid || value.binding_id !== bid || value.result_id !== rid || value.input_id !== inputId) throw new Error('cloud_receive_identity_mismatch');
    return value;
  }
  async function ensure(include_code: boolean, artifact_ids: string[], refresh = false, fixedId?: string) {
    if (flight.current) throw new Error('cloud_receive_busy');
    flight.current = true; setBusy(true); setError('');
    const abort = new AbortController(); controller.current = abort;
    try {
      const cache: Record<string, Intent> = JSON.parse(sessionStorage.getItem(key) ?? '{}');
      const selection = JSON.stringify([include_code, [...artifact_ids].sort()]);
      const old = fixedId ? { command_id: fixedId, include_code, artifact_ids: [...artifact_ids] } : cache[selection];
      const request = old || { command_id: crypto.randomUUID(), artifact_ids: [...artifact_ids], include_code };
      if (!old) { cache[selection] = request; sessionStorage.setItem(key, JSON.stringify(cache)); }
      let value: ReceiveRecord | undefined;
      if (old) {
        try { value = checked(await api.receiveStatus(sid, request.command_id, abort.signal), request); }
        catch (failure) { if (fixedId || (failure as { code?: string }).code !== 'cloud_receive_not_found') throw failure; }
      }
      if (!value) value = checked(await api.receive(sid, bid, rid, request), request);
      else if (refresh) value = checked(await api.retryReceive(sid, value.receive_id, true), request);
      else if (!value.active && ['failed', 'unknown'].includes(value.phase)) value = checked(await api.retryReceive(sid, value.receive_id), request);
      while (true) {
        if (abort.signal.aborted) throw new Error('cloud_receive_cancelled');
        if (alive.current) setRecord(value);
        if (value.phase === 'available') return value;
        if (!value.active && ['failed', 'unknown'].includes(value.phase)) throw new Error(value.error_code ?? 'cloud_material_unavailable');
        await new Promise<void>((resolve, reject) => {
          const onAbort = () => { clearTimeout(timer); reject(new Error('cloud_receive_cancelled')); };
          const timer = setTimeout(() => { abort.signal.removeEventListener('abort', onAbort); resolve(); }, backgroundTaskPollMs);
          abort.signal.addEventListener('abort', onAbort, { once: true });
        });
        value = checked(await api.receiveStatus(sid, request.command_id, abort.signal), request);
      }
    } catch (failure) { if (alive.current) { console.warn('cloud_material_prepare_failed'); setError(cloudError(failure)); } throw failure; }
    finally { flight.current = false; if (alive.current) setBusy(false); }
  }
  return { ensure, record, busy, error };
}
