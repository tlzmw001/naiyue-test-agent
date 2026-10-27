import { useCallback, useEffect, useRef, useState } from 'react';
import { errorText, type WebApi } from './api';
import type { BackgroundTaskOutput, BackgroundTaskPage } from './backgroundTaskTypes';
import { backgroundTaskPollMs } from './uiSettings';

export function useBackgroundTasks(api: WebApi, open: boolean, active = true) {
  const [visible, setVisible] = useState(document.visibilityState !== 'hidden');
  const [saved, setSaved] = useState<{ api: WebApi; page: BackgroundTaskPage }>();
  const savedRef = useRef(saved); savedRef.current = saved;
  const [selected, select] = useState<string>();
  const [detail, setDetail] = useState<{ api: WebApi; value: BackgroundTaskOutput }>();
  const [error, setError] = useState('');
  const [outputError, setOutputError] = useState('');
  const [stopFailure, setStopFailure] = useState<{ id: string; text: string }>();
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [outputLoading, setOutputLoading] = useState(false);
  const [moreLoading, setMoreLoading] = useState(false);
  const [stopping, setStopping] = useState<string>();
  const [refresh, setRefresh] = useState(0);
  const terminal = useRef<{ api: WebApi; version: number } | undefined>(undefined);
  const more = useRef<AbortController | undefined>(undefined);
  const stopRequest = useRef<AbortController | undefined>(undefined);
  const retry = useCallback(() => setRefresh(value => value + 1), []);

  useEffect(() => {
    const changed = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  useEffect(() => {
    select(undefined); setDetail(undefined); setNotice(''); setError(''); setStopFailure(undefined); setStopping(undefined);
    return () => { more.current?.abort(); stopRequest.current?.abort(); stopRequest.current = undefined; };
  }, [api]);

  useEffect(() => {
    if (!visible || !active) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      setLoading(true);
      try {
        const page = await api.backgroundTasks(undefined, controller.signal);
        if (controller.signal.aborted) return;
        const baseline = terminal.current;
        if (baseline?.api === api && page.terminal_revision > baseline.version) setNotice('后台任务已结束或需核对');
        terminal.current = { api, version: page.terminal_revision };
        setSaved(old => old?.api === api && old.page.revision === page.revision ? old : { api, page });
        setError('');
      } catch (failure) {
        if (!controller.signal.aborted) { console.warn('background_task_list_failed'); setError(errorText(failure)); }
      } finally {
        if (!controller.signal.aborted) { setLoading(false); timer = setTimeout(poll, backgroundTaskPollMs); }
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); more.current?.abort(); more.current = undefined; setMoreLoading(false); };
  }, [api, visible, refresh, active]);

  useEffect(() => {
    setOutputError('');
    if (!active || !visible || !open || !selected) { setOutputLoading(false); return; }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      setOutputLoading(true);
      try {
        const value = await api.backgroundTaskOutput(selected!, controller.signal);
        if (!controller.signal.aborted && value.task_id === selected) setDetail({ api, value });
        if (!controller.signal.aborted) setOutputError('');
      } catch (failure) {
        if (!controller.signal.aborted) { console.warn('background_task_output_failed'); setOutputError(errorText(failure)); }
      } finally {
        if (!controller.signal.aborted) { setOutputLoading(false); timer = setTimeout(poll, backgroundTaskPollMs); }
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, visible, open, selected, refresh, active]);

  const loadMore = async () => {
    const snapshot = savedRef.current;
    if (!visible || more.current || snapshot?.api !== api || !snapshot.page.next_before) return;
    const controller = new AbortController(); more.current = controller; setMoreLoading(true);
    try {
      const page = await api.backgroundTasks(snapshot.page.next_before, controller.signal);
      if (controller.signal.aborted) return;
      if (page.revision !== snapshot.page.revision) { retry(); return; }
      setSaved(old => {
        if (old?.api !== api || old.page.revision !== snapshot.page.revision) return old;
        const items = [...new Map([...old.page.items, ...page.items].map(row => [row.task_id, row])).values()];
        return { api, page: { ...page, items } };
      });
      setError('');
    } catch (failure) {
      if (!controller.signal.aborted) { console.warn('background_task_page_failed'); setError(errorText(failure)); }
    } finally {
      if (more.current === controller) { more.current = undefined; setMoreLoading(false); }
    }
  };

  const stop = async (id: string) => {
    if (stopRequest.current) return;
    const controller = new AbortController(); stopRequest.current = controller; setStopping(id); setStopFailure(undefined);
    try {
      await api.stopBackgroundTask(id, controller.signal);
    } catch (failure) {
      if (!controller.signal.aborted) { console.warn('background_task_stop_failed'); setStopFailure({ id, text: errorText(failure) }); }
    } finally {
      if (stopRequest.current === controller) {
        stopRequest.current = undefined; setStopping(undefined);
        if (!controller.signal.aborted) retry();
      }
    }
  };

  const page = saved?.api === api ? saved.page : undefined;
  const output = detail?.api === api && detail.value.task_id === selected ? detail.value : undefined;
  return { items: page?.items ?? [], activeCount: error ? undefined : page?.active_count, selected, select,
    nextBefore: page?.next_before, output, error, outputError, stopError: stopFailure?.id === selected ? stopFailure?.text ?? '' : '', loading, outputLoading,
    moreLoading, stopping, notice, dismissNotice: () => setNotice(''), retry, loadMore, stop };
}
export type BackgroundTasksState = ReturnType<typeof useBackgroundTasks>;
