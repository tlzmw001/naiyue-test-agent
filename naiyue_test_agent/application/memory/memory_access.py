"""Application-owned workspace/personal reads; no user-facing write bypass."""

import base64
import binascii
import hashlib
import json
import logging
import sqlite3
import time

from .personal_memory import PersonalMemory
from naiyue_test_agent.contracts.memory import MemoryReadError
from . import memory_results as public
from .memory_lexical import select
from naiyue_test_agent.contracts import memory_lexical as lexical


logger = logging.getLogger(__name__)


def _encode(value):
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode()


def _decode(token, expected):
    if token is None:return None
    try:
        value = json.loads(base64.b64decode(token, altchars=b'-_', validate=True))
        if not isinstance(value, dict) or value.get('binding') != expected:
            raise ValueError('binding')
        before = value['before']
        if (not isinstance(before, list) or len(before) != 3 or type(before[0]) not in (int, float)
                or before[1] not in ('workspace', 'personal') or not isinstance(before[2], str)):
            raise ValueError('position')
        return before
    except (ValueError, UnicodeError, binascii.Error, TypeError, KeyError):
        raise MemoryReadError('memory_cursor_stale') from None


class MemoryAccess:
    def __init__(self, storage, settings, data_dir, *, personal=None, personal_only=False):
        self.storage, self.settings = storage, settings
        self._personal_store = personal if personal is not None else PersonalMemory(data_dir, settings)
        self._owns_personal, self.personal_only = personal is None, personal_only
        self._lock = self._personal_store.lock
        self._closed = False

    def close(self):
        with self._lock:
            self._closed = True
            if self._owns_personal:
                self._personal_store.close()

    def _stores(self, requested):
        result = []
        if requested in ('both', 'workspace') and not self.personal_only:
            result.append(('workspace', self.storage.memory))
        if requested in ('both', 'personal'):
            result.append(('personal', self._personal_store.repository()))
        return result


    def query(self, source_session_id, project_id, request, *, now=None):
        try:
            with self._lock:
                if self._closed or not self.settings.enabled:
                    raise MemoryReadError('memory_unavailable')
                binding = self.storage.sessions.get_workspace_binding()
                source = self.storage.sessions.get(source_session_id)
                if self.personal_only:
                    if (binding is not None or project_id is not None or source['project_id'] is not None
                            or source['workspace_root'] is not None):
                        raise MemoryReadError('memory_scope_mismatch')
                    if request['scope'] == 'workspace' or request['action'] == 'refresh_overview':
                        raise MemoryReadError('memory_scope_mismatch')
                    namespace = 'chats'
                elif (not binding or project_id is None or source['project_id'] != project_id
                        or binding['workspace_id'] != project_id):
                    raise MemoryReadError('memory_scope_mismatch')
                else:
                    namespace = binding['workspace_id']
                if request['action'] in ('record','update','disable','forget'):
                    return self._mutate(namespace,source_session_id,request,time.time() if now is None else now)
                count = request['limit'] or self.settings.default_limit
                variants = tuple(dict.fromkeys((request['query'], *request['expansions']))) if request['query'] else ()
                if count > self.settings.max_limit:
                    raise MemoryReadError('memory_invalid_limit')
                if any(len(q) > self.settings.max_query_chars for q in variants):
                    raise MemoryReadError('memory_query_too_complex')
                stores = self._stores(request['scope'])
                return self._query(stores, namespace, source_session_id, request,
                                   time.time() if now is None else now, count, variants)
        except MemoryReadError:
            raise
        except (sqlite3.Error, OSError, ValueError) as error:
            logger.error('memory_read_failed error_type=%s', type(error).__name__)
            code = 'memory_busy' if isinstance(error, sqlite3.OperationalError) and getattr(error,
                'sqlite_errorcode', None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else 'memory_write_failed' if request['action'] in ('record','update','disable','forget') else 'memory_read_failed'
            raise MemoryReadError(code) from error

    def _mutate(self, workspace_id, caller, request, now):
        from .memory_mutations import commit
        _, target = self._stores(request['scope'])[0]
        with target.edit_with_sources(self.storage.memory) as (writer, archive):
            result = commit(writer, archive, workspace_id, caller, request, self.settings, now)
            return public.fit_mutation(result, self.settings.max_output_bytes)

    def _query(self, stores, workspace_id, source, request, now, count, variants):
        selector = {key: value for key, value in request.items() if key not in ('cursor', 'offset')}
        fingerprint = hashlib.sha256(json.dumps([workspace_id, source, selector], sort_keys=True).encode()).hexdigest()
        before = _decode(request['cursor'], fingerprint)
        rows, truncated = [], False
        if request['action'] == 'search':
            rows, truncated = self._search(stores, workspace_id, request, now, variants, count)
        elif request['action'] == 'list':
            for scope, store in stores:
                rows.extend(store.listing(scope, workspace_id, request, now, before, count + 1))
            rows.sort(key=lambda row: (-row['updated_at'], row['scope'], row['memory_id']))
            truncated = len(rows) > count
            rows = rows[:count]
        else:
            scope, store = stores[0]
            row = store.current(scope, workspace_id, request, now, request['memory_id'])
            if row is None:raise MemoryReadError('memory_not_found')
            if request['version'] is not None and row['version'] != request['version']:
                raise MemoryReadError('memory_version_changed')
            rows = [row]
        result = dict(status='ok', items=[], requested_limit=count, truncated=truncated, has_more=False, next_cursor=None)
        store_map = dict(stores)
        for i, row in enumerate(rows):
            source_rows, source_more = store_map[row['scope']].sources(
                row['memory_id'], row['version'], self.settings.source_limit)
            item = public.record(row, request, settings=self.settings, source_rows=source_rows,
                sources_truncated=source_more, storage=self.storage, workspace_id=workspace_id)
            if request['action']=='read' and request.get('include_history'):
                versions = store_map[row['scope']].versions(row['memory_id'])
                item['history'], item['history_truncated'] = versions[:5], len(versions)>5
            old_cursor = result['next_cursor']
            if request['action'] == 'list':
                more = truncated or i + 1 < len(rows)
                result.update(has_more=more, next_cursor=_encode(dict(binding=fingerprint,
                    before=[row['updated_at'], row['scope'], row['memory_id']])) if more else None)
            if not public.fit(result, item, self.settings.max_output_bytes):
                result.update(truncated=True)
                if request['action'] == 'list':result.update(has_more=True, next_cursor=old_cursor)
                break
        if request['action'] == 'read':
            result['has_more'] = not result['items'][0]['complete']
        if public.size(result) > self.settings.max_output_bytes:
            raise MemoryReadError('memory_output_budget')
        return result


    def _search(self, stores, workspace_id, request, now, variants, count):
        contributions, truncated = {}, False
        for variant in variants:
            for scope, store in stores:
                ranked, more = store.rank(scope, workspace_id, request, now, variant, self.settings)
                truncated |= more
                for rank, row in enumerate(ranked, 1):
                    proof = (row['version'], lexical.digest(row['title'], row['aliases_json'], row['body']))
                    contributions.setdefault((scope, row['memory_id']), []).append((proof, variant,
                        1 / (self.settings.rrf_k + rank)))
        candidates, store_map = [], dict(stores)
        for (scope, identity), values in contributions.items():
            row = store_map[scope].current(scope, workspace_id, request, now, identity)
            if row is None:continue
            proof = (row['version'], lexical.digest(row['title'], row['aliases_json'], row['body']))
            valid = [(variant, score) for saved, variant, score in values if saved == proof]
            if not valid:continue
            candidates.append({**row, 'score':sum(score for _, score in valid),
                               'matched_variants':list(dict.fromkeys(v for v, _ in valid))})
        candidates.sort(key=lambda row: (-row['score'], row['scope'], row['memory_id']))
        selected, more = select(candidates, variants, limit=count, coverage=self.settings.fallback_coverage)
        return selected, truncated or more
