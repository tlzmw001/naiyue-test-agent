import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './api';
import type { CacheCleanupApi } from './cacheCleanupApi';
import { cleanupCategories } from './cacheCleanupTypes';
import type { CleanupCategory, CleanupPolicy, CleanupResult } from './cacheCleanupTypes';
import { sessionSyncPollMs } from './uiSettings';

type Identity = { requestId: string; instanceId: string; categories: CleanupCategory[] };
type Operation = Identity & { phase: 'submitting' | 'checking' | 'finished' | 'unknown'; result?: CleanupResult; error?: string };
const storageKey = 'naiyue.cache-cleanup.last';
const unknown = '无法确认原操作结果。';

function readSaved(): Identity | undefined {
  const raw = sessionStorage.getItem(storageKey);
  if (!raw) return;
  const saved = JSON.parse(raw);
  if (saved && typeof saved.instanceId === 'string' && typeof saved.requestId === 'string' &&
      /^[0-9a-f-]{36}$/.test(saved.requestId) && Array.isArray(saved.categories) &&
      saved.categories.length > 0 && saved.categories.every((value: CleanupCategory) => cleanupCategories.includes(value))) {
    return saved;
  }
}

export function useCacheCleanup(api: CacheCleanupApi, instanceId: string, open: boolean) {
  const [policies, setPolicies] = useState<CleanupPolicy[]>();
  const [policyError, setPolicyError] = useState(false);
  const [policyRevision, setPolicyRevision] = useState(0);
  const [storageError, setStorageError] = useState(false);
  const [selected, setSelected] = useState<CleanupCategory[]>([]);
  const [operation, setOperation] = useState<Operation>();
  const owned = useRef<Operation | undefined>(undefined);
  const mounted = useRef(false);

  const publish = useCallback((value: Operation) => {
    owned.current = value;
    if (mounted.current) setOperation(value);
  }, []);

  useEffect(() => {
    mounted.current = true;
    if (!owned.current) {
      try {
        const saved = readSaved();
        if (saved) {
          setSelected(saved.categories);
          publish({ ...saved, phase: saved.instanceId === instanceId ? 'checking' : 'unknown' });
        }
      } catch {
        console.warn('cache_cleanup_storage_read_failed');
        setStorageError(true);
      }
    }
    return () => { mounted.current = false; };
  }, [instanceId, publish]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setPolicies(undefined);
    setPolicyError(false);
    void api.policies(controller.signal).then(data => {
      if (controller.signal.aborted) return;
      setPolicies(data.categories);
      setSelected(previous => previous.filter(id => data.categories.some(policy => policy.id === id && policy.available)));
    }).catch(() => {
      if (!controller.signal.aborted) setPolicyError(true);
    });
    return () => controller.abort();
  }, [api, open, policyRevision]);

  useEffect(() => {
    if (operation?.phase !== 'checking') return;
    if (operation.instanceId !== instanceId) {
      publish({ ...operation, phase: 'unknown' });
      return;
    }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const lookup = async () => {
      try {
        const reply = await api.lookup(operation.requestId, controller.signal);
        if (controller.signal.aborted) return;
        if (reply.state === 'running') timer = setTimeout(lookup, sessionSyncPollMs);
        else publish({ ...operation, phase: 'finished', result: reply.result, error: reply.error?.code });
      } catch (error) {
        if (!controller.signal.aborted) publish({ ...operation, phase: 'unknown',
          error: error instanceof ApiError && error.code === 'service_instance_mismatch' ? error.code : undefined });
      }
    };
    void lookup();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, instanceId, operation, publish]);

  const reloadPolicies = () => {
    setPolicies(undefined);
    setPolicyRevision(value => value + 1);
  };

  const execute = async () => {
    if (owned.current?.phase === 'submitting' || owned.current?.phase === 'checking') return;
    const categories = cleanupCategories.filter(id => selected.includes(id) && policies?.some(p => p.id === id && p.available));
    if (!categories.length || categories.length !== selected.length) return;
    const next: Operation = { requestId: crypto.randomUUID(), instanceId, categories, phase: 'submitting' };
    publish(next);
    try {
      sessionStorage.setItem(storageKey, JSON.stringify({ requestId: next.requestId, instanceId, categories }));
    } catch {
      console.warn('cache_cleanup_storage_write_failed');
      setStorageError(true);
    }
    try {
      const result = await api.execute(next.requestId, categories);
      publish({ ...next, phase: 'finished', result });
    } catch (error) {
      if (error instanceof ApiError && !['http_failed', 'command_result_unavailable', 'operation_failed'].includes(error.code)) {
        publish({ ...next, phase: 'finished', error: error.code });
      } else {
        publish({ ...next, phase: 'checking' });
      }
    } finally {
      if (mounted.current) reloadPolicies();
    }
  };

  return { policies, policyError, reloadPolicies, storageError, selected, setSelected, operation,
    pending: operation?.phase === 'checking' || operation?.phase === 'submitting',
    unknown, execute, canRecheck: operation?.phase === 'unknown' && operation.instanceId === instanceId &&
      operation.error !== 'service_instance_mismatch',
    recheck: () => { if (operation?.phase === 'unknown') publish({ ...operation, phase: 'checking' }); } };
}
