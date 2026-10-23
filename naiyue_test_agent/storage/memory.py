"""Memory content on either database; project evidence stays on its owning archive."""
from contextlib import contextmanager
import json
import logging
import sqlite3

from naiyue_test_agent.application.memory import memory_queries as queries
from naiyue_test_agent.application.memory import memory_overview_store as overview_sql
from naiyue_test_agent.application.memory.memory_queries import MemoryReadError

logger = logging.getLogger(__name__)


class MemoryRepo:
    def __init__(self, database):
        self.database = database

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

    def build_batch(self, **query):
        from naiyue_test_agent.application.memory.memory_batches import build_batch
        try:
            with self.database.transaction() as connection:
                return build_batch(connection, **query)
        except sqlite3.Error as error:
            logger.error('memory_batch_read_failed error_type=%s', type(error).__name__)
            raise MemoryReadError('memory_batch_read_failed') from error

    def listing(self, scope, workspace_id, request, now, before, limit):
        with self.database.transaction() as connection:
            return queries.listing(connection, scope, workspace_id, request, now, before, limit)

    def current(self, scope, workspace_id, request, now, identity):
        with self.database.transaction() as connection:
            return queries.current(connection, scope, workspace_id, request, now, identity)

    def sources(self, identity, version, limit):
        with self.database.transaction() as connection:
            return queries.sources(connection, identity, version, limit)

    def rank(self, scope, workspace_id, request, now, variant, settings):
        with self.database.transaction() as connection:
            return queries.rank(connection, scope, workspace_id, request, now, variant, settings)

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

    def mutate_with_sources(self, archive, workspace_id, caller, request, settings, now):
        from naiyue_test_agent.application.memory.memory_mutations import commit
        from naiyue_test_agent.application.memory.memory_results import fit_mutation
        if self.database is archive.database:
            with self.database.transaction(write=True) as connection:
                return fit_mutation(commit(connection, connection, workspace_id, caller, request, settings, now), settings.max_output_bytes)
        # Source snapshot first, then target write lock: preserve project -> personal ordering.
        with archive.database.transaction() as source, self.database.transaction(write=True) as target:
            return fit_mutation(commit(target, source, workspace_id, caller, request, settings, now), settings.max_output_bytes)
