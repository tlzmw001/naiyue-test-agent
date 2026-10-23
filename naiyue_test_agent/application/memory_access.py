"""Application-owned workspace/personal reads; no user-facing write bypass."""

import base64
import binascii
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from threading import RLock
import time

from .memory_personal import PersonalMemory
from .memory_queries import MemoryReadError
from . import memory_queries as queries, memory_results as public, memory_lexical as lexical


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
    def __init__(self, storage, settings, data_dir):
        self.storage, self.settings = storage, settings
        self._path = Path(data_dir) / 'personal' / 'memory.sqlite'
        self._personal = None
        self._lock = RLock()
        self._closed = False

    def close(self):
        with self._lock:
            self._closed = True
            if self._personal is not None:
                self._personal.close()

    def _stores(self, requested):
        result = []
        if requested in ('both', 'workspace'):
            result.append(('workspace', self.storage))
        if requested in ('both', 'personal'):
            if self._personal is None:
                self._personal = PersonalMemory(self._path, self.settings)
            result.append(('personal', self._personal))
        return result

    @staticmethod
    def _read(store, function, *args):
        with store._lock, store.connection:
            store.connection.execute('BEGIN')
            return function(store.connection, *args)

    def query(self, source_session_id, project_id, request, *, now=None):
        try:
            with self._lock:
                if self._closed or not self.settings.enabled:
                    raise MemoryReadError('memory_unavailable')
                binding = self.storage.get_workspace_binding()
                source = self.storage.get(source_session_id)
                if (not binding or project_id is None or source['project_id'] != project_id
                        or binding['workspace_id'] != project_id):
                    raise MemoryReadError('memory_scope_mismatch')
                if request['action'] in ('record','update','disable','forget'):
                    return self._mutate(binding['workspace_id'],source_session_id,request,time.time() if now is None else now)
                count = request['limit'] or self.settings.default_limit
                variants = tuple(dict.fromkeys((request['query'], *request['expansions']))) if request['query'] else ()
                if count > self.settings.max_limit:
                    raise MemoryReadError('memory_invalid_limit')
                if any(len(q) > self.settings.max_query_chars for q in variants):
                    raise MemoryReadError('memory_query_too_complex')
                stores = self._stores(request['scope'])
                return self._query(stores, binding['workspace_id'], source_session_id, request,
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
        _, store = self._stores(request['scope'])[0]
        with self.storage._lock:
            if store is self.storage:
                with store.connection:
                    store.connection.execute('BEGIN IMMEDIATE')
                    return public.fit_mutation(commit(store.connection,store.connection,workspace_id,caller,request,self.settings,now),self.settings.max_output_bytes)
            with self.storage.connection, store._lock, store.connection:
                self.storage.connection.execute('BEGIN')
                store.connection.execute('BEGIN IMMEDIATE')
                return public.fit_mutation(commit(store.connection,self.storage.connection,workspace_id,caller,request,self.settings,now),self.settings.max_output_bytes)

    def _query(self, stores, workspace_id, source, request, now, count, variants):
        selector = {key: value for key, value in request.items() if key not in ('cursor', 'offset')}
        fingerprint = hashlib.sha256(json.dumps([workspace_id, source, selector], sort_keys=True).encode()).hexdigest()
        before = _decode(request['cursor'], fingerprint)
        rows, truncated = [], False
        if request['action'] == 'search':
            rows, truncated = self._search(stores, workspace_id, request, now, variants, count)
        elif request['action'] == 'list':
            for scope, store in stores:
                rows.extend(self._read(store, queries.listing, scope, workspace_id, request, now, before, count + 1))
            rows.sort(key=lambda row: (-row['updated_at'], row['scope'], row['memory_id']))
            truncated = len(rows) > count
            rows = rows[:count]
        else:
            scope, store = stores[0]
            row = self._read(store, queries.current, scope, workspace_id, request, now, request['memory_id'])
            if row is None:raise MemoryReadError('memory_not_found')
            if request['version'] is not None and row['version'] != request['version']:
                raise MemoryReadError('memory_version_changed')
            rows = [row]
        result = dict(status='ok', items=[], requested_limit=count, truncated=truncated, has_more=False, next_cursor=None)
        store_map = dict(stores)
        for i, row in enumerate(rows):
            source_rows, source_more = self._read(store_map[row['scope']], queries.sources,
                row['memory_id'], row['version'], self.settings.source_limit)
            item = public.record(row, request, settings=self.settings, source_rows=source_rows,
                sources_truncated=source_more, storage=self.storage, workspace_id=workspace_id)
            if request['action']=='read' and request.get('include_history'):
                versions = self._read(store_map[row['scope']], self._versions,row['memory_id'])
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

    @staticmethod
    def _versions(connection, memory_id):
        rows=connection.execute('SELECT snapshot_json FROM naiyue_memory_versions WHERE memory_id=? ORDER BY version DESC LIMIT 6', (memory_id,))
        result=[]
        for saved in rows:
            snapshot=json.loads(saved[0])
            result.append({key:(value[:300] if key=='body' else value) for key,value in snapshot.items() if key in
                ('version','status','title','body','updated_at','evidence_at','expires_at','reason','deadline_at')} |
                {'gaps':json.loads(snapshot['gaps_json'])})
        return result

    def _search(self, stores, workspace_id, request, now, variants, count):
        contributions, truncated = {}, False
        for variant in variants:
            for scope, store in stores:
                ranked, more = self._read(store, queries.rank, scope, workspace_id, request, now, variant, self.settings)
                truncated |= more
                for rank, row in enumerate(ranked, 1):
                    proof = (row['version'], lexical.digest(row['title'], row['aliases_json'], row['body']))
                    contributions.setdefault((scope, row['memory_id']), []).append((proof, variant,
                        1 / (self.settings.rrf_k + rank)))
        candidates, store_map = [], dict(stores)
        for (scope, identity), values in contributions.items():
            row = self._read(store_map[scope], queries.current, scope, workspace_id, request, now, identity)
            if row is None:continue
            proof = (row['version'], lexical.digest(row['title'], row['aliases_json'], row['body']))
            valid = [(variant, score) for saved, variant, score in values if saved == proof]
            if not valid:continue
            candidates.append({**row, 'score':sum(score for _, score in valid),
                               'matched_variants':list(dict.fromkeys(v for v, _ in valid))})
        candidates.sort(key=lambda row: (-row['score'], row['scope'], row['memory_id']))
        selected, more = lexical.select(candidates, variants, limit=count, coverage=self.settings.fallback_coverage)
        return selected, truncated or more
