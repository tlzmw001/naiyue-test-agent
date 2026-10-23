"""Necessary reply boundaries and regeneration state."""
from naiyue_test_agent.application.conversation import reply_storage as sql


class ReplyRepo:
    def __init__(self, database):
        self.database = database

    def latest(self, session_id):
        with self.database.transaction(write=False) as connection:
            return sql.latest(connection, session_id)

    def action(self, session_id, request_id):
        with self.database.transaction(write=False) as connection:
            return sql.action(connection, session_id, request_id)

    def record_action(self, session_id, request_id, reply, revision, new_thread, run_id):
        with self.database.transaction(write=True) as connection:
            return sql.record_action(connection, session_id, request_id, reply, revision, new_thread, run_id)

    def activate(self, session_id, request_id, *, checkpoint, head_checkpoint, boundary):
        with self.database.transaction(write=True, error_code='session_save_unconfirmed') as connection:
            return sql.activate(connection, session_id, request_id, checkpoint=checkpoint, head_checkpoint=head_checkpoint, boundary=boundary)

    def all_actions(self, session_id):
        with self.database.transaction(write=False) as connection:
            return sql.all_actions(connection, session_id)

    def fail_preparation(self, session_id, request_id):
        with self.database.transaction(write=True) as connection:
            return sql.fail_preparation(connection, session_id, request_id)
