"""Memory content on either database; project evidence stays on its owning archive."""
from contextlib import contextmanager
import json
import logging
import sqlite3

from naiyue_test_agent.storage.memory_sql import memory_queries as queries
from naiyue_test_agent.storage.memory_sql import memory_overview_store as overview_sql
from naiyue_test_agent.contracts.memory import MemoryReadError
from .memory_sql.archive import ArchiveReader
from .memory_sql.writes import MemoryWriter

logger = logging.getLogger(__name__)


class MemoryRepo:
    def __init__(self, database):
        self.database = database
        self._retention_scan = {}

    @contextmanager
    def _overview_transaction(self, *, write=False):
        try:
            with self.database.transaction(write=write) as connection:
                yield connection
        except sqlite3.Error as error:
            logger.error('memory_overview_storage_failed error_type=%s', type(error).__name__)
            raise MemoryReadError('memory_overview_storage_failed') from error

    def ensure(self, session_id):
        with self._overview_transaction(write=True) as connection:
            return overview_sql.ensure(connection, session_id)

    def tick(self, session_id):
        with self._overview_transaction(write=True) as connection:
            return overview_sql.tick(connection, session_id)

    def accept(self, session_id, *, thread_id, conversation_revision, upper):
        with self._overview_transaction(write=True) as connection:
            return overview_sql.accept(connection, session_id, thread_id=thread_id, conversation_revision=conversation_revision, upper=upper)

    def mark_directory(self, session_id):
        with self._overview_transaction(write=True) as connection:
            return overview_sql.mark_directory(connection, session_id)

    def overview(self, session_id):
        with self._overview_transaction(write=False) as connection:
            return overview_sql.overview(connection, session_id)

    def save_overview(self, *, session_id, thread_id, conversation_revision, overview, lower, upper, task_id, origin_run_id, batch, skipped, expected_history_version=None):
        with self._overview_transaction(write=True) as connection:
            return overview_sql.save_overview(connection, session_id=session_id, thread_id=thread_id, conversation_revision=conversation_revision, overview=overview, lower=lower, upper=upper, task_id=task_id, origin_run_id=origin_run_id, batch=batch, skipped=skipped, expected_history_version=expected_history_version)

    def directory(self, session_id, limit):
        with self._overview_transaction(write=False) as connection:
            return overview_sql.directory(connection, session_id, limit)

    def progress(self, session_id):
        return self.database.read_rows('SELECT * FROM naiyue_memory_progress WHERE session_id=?', (session_id,))

    @contextmanager
    def batch_reader(self, sensitive_pattern):
        try:
            with self.database.transaction() as connection:
                connection.create_function('naiyue_memory_sensitive', 1,
                    lambda text: bool(sensitive_pattern.search(text)), deterministic=True)
                yield ArchiveReader(connection)
        except sqlite3.Error as error:
            logger.error('memory_batch_read_failed error_type=%s', type(error).__name__)
            raise MemoryReadError('memory_batch_read_failed') from error

    def listing(self, scope, workspace_id, request, now, before, limit):
        with self.database.transaction() as connection:
            if 'kind' not in request:
                from .memory_sql.memory_wiki_schema import owner
                owner(connection,scope,workspace_id if scope=='workspace' else None)
            return queries.listing(connection, scope, workspace_id, request, now, before, limit)

    def current(self, scope, workspace_id, request, now, identity):
        with self.database.transaction() as connection:
            if 'kind' not in request:
                from .memory_sql.memory_wiki_schema import owner
                owner(connection,scope,workspace_id if scope=='workspace' else None)
            return queries.current(connection, scope, workspace_id, request, now, identity)

    def sources(self, identity, version, limit):
        with self.database.transaction() as connection:
            return queries.sources(connection, identity, version, limit)

    def rank(self, scope, workspace_id, request, now, variant, settings):
        with self.database.transaction() as connection:
            if 'kind' not in request:
                from .memory_sql.memory_wiki_schema import owner
                owner(connection,scope,workspace_id if scope=='workspace' else None)
            return queries.rank(connection, scope, workspace_id, request, now, variant, settings)

    def any_suppressed(self, source_hashes):
        with self.database.transaction() as connection:
            writer = MemoryWriter(connection)
            return any(writer.suppressed(value) for value in source_hashes)

    def rank_comparison(self, scope, workspace_id, now, variant, required, settings):
        with self.database.transaction() as connection:
            return queries.rank_comparison(connection,scope,workspace_id,now,variant,required,settings)

    def versions(self, memory_id):
        with self.database.transaction() as connection:
            rows = connection.execute('SELECT snapshot_json FROM naiyue_memory_versions WHERE memory_id=? ORDER BY version DESC LIMIT 6', (memory_id,))
            result = []
            for saved in rows:
                snapshot = json.loads(saved[0])
                result.append({key: (value[:300] if key == 'body' else value) for key, value in snapshot.items() if key in
                    ('version','status','title','body','updated_at','evidence_at','expires_at','reason','deadline_at')} |
                    {'gaps': json.loads(snapshot['gaps_json'])})
            return result

    @contextmanager
    def edit_with_sources(self, archive):
        if self.database is archive.database:
            with self.database.transaction(write=True) as connection:
                yield MemoryWriter(connection), ArchiveReader(connection)
            return
        # Source snapshot first, then target write lock: preserve project -> personal ordering.
        with archive.database.transaction() as source, self.database.transaction(write=True) as target:
            yield MemoryWriter(target), ArchiveReader(source)

    def prepare_wiki(self, *, now):
        """Explicit format preparation under the existing owner; startup is wired in S6."""
        from .memory_sql import memory_schema, memory_wiki_schema, memory_wiki_migration
        from .backup import create_backup
        from .memory_sql.memory_personal_upgrade import backup_format
        with self.database.lock:
            c=self.database.connection
            try:
                info=c.execute('SELECT * FROM naiyue_memory_info').fetchone()
                if info['format_version']==5:
                    memory_wiki_schema.check(c)
                    return dict(generation_id=info['generation_id'],backup=None)
                memory_schema.check(c,version=4)
                project=c.execute("SELECT 1 FROM sqlite_master WHERE name='naiyue_store_info'").fetchone()
                scope='workspace' if project else 'personal'
                from .queries import workspace_binding
                binding=workspace_binding(c) if project else None
                if project and binding is None:raise MemoryReadError('memory_scope_mismatch')
                workspace_id=binding['workspace_id'] if binding else None
                backup=None
                if project and self.database.path is not None:
                    backup=create_backup(c,self.database.path,target_schema=26)
                with self.database.transaction(write=True):
                    if not project:
                        backup=str(backup_format(self.database.path,version=4))
                    memory_wiki_migration.upgrade(c,scope=scope,workspace_id=workspace_id,now=now)
                    generation=c.execute('SELECT generation_id FROM naiyue_memory_info').fetchone()[0]
                return dict(generation_id=generation,backup=backup)
            except Exception as error:
                logger.error('memory_wiki_prepare_failed error_type=%s',type(error).__name__)
                raise

    @contextmanager
    def _wiki_transaction(self, *, write=False):
        try:
            with self.database.transaction(write=write) as c:
                yield c
        except sqlite3.Error as error:
            logger.error('memory_wiki_storage_failed error_type=%s',type(error).__name__)
            raise MemoryReadError('memory_wiki_storage_failed') from error

    @contextmanager
    def wiki_edit(self, scope, workspace_id, *, now, capacity):
        from .memory_sql.memory_wiki_writes import WikiWriter
        with self._wiki_transaction(write=True) as c:
            writer=WikiWriter(c,scope,workspace_id,now=now,capacity=capacity)
            yield writer
            if writer.rollback_failed:
                raise MemoryReadError('memory_wiki_group_rollback_failed')

    def wiki_pages(self,scope,workspace_id,topic,**options):
        from .memory_sql.memory_wiki_queries import pages
        with self._wiki_transaction() as c:
            return pages(c,scope,workspace_id,topic,**options)

    def wiki_members(self,scope,workspace_id,page_id,**options):
        from .memory_sql.memory_wiki_queries import members
        with self._wiki_transaction() as c:
            return members(c,scope,workspace_id,page_id,**options)

    def wiki_sources(self,identities):
        from .memory_sql.memory_wiki_queries import sources
        with self._wiki_transaction() as c:
            return sources(c,identities)

    def wiki_links(self,scope,workspace_id,page_id,*,now):
        from .memory_sql.memory_wiki_queries import links
        with self._wiki_transaction() as c:
            return links(c,scope,workspace_id,page_id,now=now)

    def wiki_view(self,scope,workspace_id,request,**options):
        from .memory_sql.memory_wiki_queries import view
        with self._wiki_transaction() as c:
            return view(c,scope,workspace_id,request,**options)

    def wiki_targets(self,scope,workspace_id,ids,*,now):
        from .memory_sql.memory_wiki_queries import targets
        with self._wiki_transaction() as c:
            return targets(c,scope,workspace_id,ids,now=now)

    @contextmanager
    def wiki_edit_with_sources(self,archive,scope,workspace_id,*,now,capacity):
        from .memory_sql.memory_wiki_writes import WikiWriter
        # Reuse the existing project-source -> personal-target transaction ownership.
        with self.edit_with_sources(archive) as (transaction,reader):
            writer=WikiWriter(transaction._connection,scope,workspace_id,now=now,capacity=capacity)
            yield writer,reader
            if writer.rollback_failed:raise MemoryReadError('memory_wiki_group_rollback_failed')

    def wiki_page(self,scope,workspace_id,page_id):
        from .memory_sql.memory_wiki_queries import page
        with self._wiki_transaction() as c:
            return page(c,scope,workspace_id,page_id)

    def prune_expired(self, *, now, grace_days, batch_size, missing_personal=(), scan_state=None):
        from .memory_sql.memory_retention import prune
        if type(grace_days) is not int or grace_days<0 or type(batch_size) is not int or not 1<=batch_size<=1000:
            raise ValueError('memory_cleanup_invalid')
        state=self._retention_scan if scan_state is None else scan_state
        pending=dict(state)
        with self._wiki_transaction(write=True) as connection:
            result=prune(connection,now=now,grace_days=grace_days,batch_size=batch_size,missing_personal=missing_personal,scan_state=pending)
        state.update(pending)
        return result

    def wiki_external_links(self, limit, after=None):
        with self._wiki_transaction() as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='naiyue_memory_info'").fetchone():return []
            if c.execute('SELECT format_version FROM naiyue_memory_info').fetchone()[0]!=5:return []
            return [r[0] for r in c.execute("SELECT DISTINCT to_page_id FROM naiyue_memory_page_links WHERE to_scope='personal' AND (? IS NULL OR to_page_id>?) ORDER BY to_page_id LIMIT ?",(after,after,limit))]

    def wiki_existing_pages(self, identities):
        with self._wiki_transaction() as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='naiyue_memory_pages'").fetchone():return None
            return {r[0] for r in c.execute('SELECT page_id FROM naiyue_memory_pages WHERE page_id IN (SELECT value FROM json_each(?))',(json.dumps(identities),))}
