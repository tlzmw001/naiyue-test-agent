"""Optional model usage facts, independent of required run saving."""

import logging
import sqlite3

from naiyue_test_agent.storage.errors import SessionPersistenceError

from naiyue_test_agent.storage.observability_sql import model_usage_storage


logger = logging.getLogger(__name__)


class UsageRepo:
    def __init__(self, database):
        self.database = database

    def save_model_usage_event(self, event):
        with self.database.transaction(write=True) as connection:
            model_usage_storage.save_event(connection, event)

    def finish_model_usage_run(self, session_id, run_id, **summary):
        with self.database.transaction(write=True) as connection:
            return model_usage_storage.finish_run(connection, session_id, run_id, **summary)

    def read_model_usage(self, session_id, *, model_limit, after_model=None):
        with self.database.transaction(write=False, error_code='usage_read_failed') as connection:
            try:
                return model_usage_storage.read_summary(connection, session_id,
                    model_limit=model_limit, after_model=after_model)
            except sqlite3.Error as error:
                logger.error('model_usage_read_failed error_type=%s', type(error).__name__)
                raise SessionPersistenceError('usage_read_failed') from error
