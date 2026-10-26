"""Environment repository shares the registry Database and its short transactions."""
from dataclasses import asdict
import json
import sqlite3
from naiyue_test_agent.contracts.sandbox import SandboxRecord, SandboxError


def _record(row):
    return SandboxRecord(**json.loads(row['record_json']))


class EnvironmentRegistry:
    def __init__(self, database):
        self.database=database

    def reserve(self, record):
        try:
            with self.database.transaction(write=True) as connection:
                connection.execute('INSERT INTO execution_environments VALUES(?,?,?,?,?)',
                    (record.binding_id,record.project_id,record.session_id,record.lifecycle,
                     json.dumps(asdict(record),sort_keys=True)))
        except sqlite3.IntegrityError as error:
            raise SandboxError('env_already_exists') from error

    def get(self, binding_id):
        rows=self.database.read_rows('SELECT record_json FROM execution_environments WHERE binding_id=?',(binding_id,))
        if not rows: raise SandboxError('env_not_registered')
        return _record(rows[0])

    def list(self, project_id):
        return tuple(_record(row) for row in self.database.read_rows(
            'SELECT record_json FROM execution_environments WHERE project_id=? ORDER BY rowid',(project_id,)))

    def save(self, record):
        with self.database.transaction(write=True) as connection:
            changed=connection.execute('''UPDATE execution_environments SET lifecycle=?,record_json=?
                WHERE binding_id=? AND project_id=? AND session_id=?''',
                (record.lifecycle,json.dumps(asdict(record),sort_keys=True),record.binding_id,record.project_id,record.session_id))
            if changed.rowcount!=1: raise SandboxError('env_not_registered')
