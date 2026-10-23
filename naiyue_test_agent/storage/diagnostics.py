"""Optional diagnostic facts and retention."""

import logging


from naiyue_test_agent.application.observability import diagnostic_storage


logger = logging.getLogger(__name__)


class DiagnosticRepo:
    def __init__(self, database):
        self.database = database

    def prepare_diagnostics(self, settings):
        with self.database.transaction(write=True) as connection:
            return diagnostic_storage.prepare(connection, settings)

    def save_diagnostic_event(self, record, settings):
        with self.database.transaction(write=True) as connection:
            return diagnostic_storage.save_event(connection, record, settings)

    def finish_diagnostic_run(self, record, settings, **summary):
        with self.database.transaction(write=True) as connection:
            return diagnostic_storage.finish_run(connection, record, settings, **summary)

    def prune_diagnostics(self, settings, *, now=None):
        with self.database.transaction(write=True) as connection:
            return diagnostic_storage.prune(connection, settings, now=now)

    def read_diagnostic_runs(self, session_id, *, limit=50, before=None):
        with self.database.transaction(write=False) as connection:
            return diagnostic_storage.read_runs(connection, session_id, limit=limit, before=before)

    def read_diagnostic_events(self, session_id, run_id, *, limit=50, after=0):
        with self.database.transaction(write=False) as connection:
            return diagnostic_storage.read_events(connection, session_id, run_id, limit=limit, after=after)
