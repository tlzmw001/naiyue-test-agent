"""Versioned subagent instance persistence."""
from .queries import subagent_row, subagent_references, read_rows
from naiyue_test_agent.application.subagents import subagent_storage as sql


class SubagentRepo:
    def __init__(self, database):
        self.database = database

    def get_subagent(self, session_id, agent_id):
        with self.database.transaction(error_code='session_read_failed') as connection:
            return subagent_row(connection, session_id, agent_id)

    def get_subagent_references(self, session_id):
        with self.database.transaction(error_code='session_read_failed') as connection:
            return subagent_references(connection, session_id)

    def for_parent_run(self, session_id, run_id):
        with self.database.transaction(error_code='session_read_failed') as connection:
            return read_rows(connection, 'SELECT session_id,agent_id,agent,child_thread_id,agent_path FROM naiyue_subagent_instances WHERE session_id=? AND last_parent_run_id=?', (session_id, run_id))

    def save_subagent(self, record, expected_version):
        with self.database.transaction(write=True, error_code='subagent_save_unconfirmed') as connection:
            return sql.save(connection, record, expected_version)

    def cancel_subagent_claim(self, record, previous):
        with self.database.transaction(write=True, error_code='subagent_save_unconfirmed') as connection:
            return sql.cancel_claim(connection, record, previous)
