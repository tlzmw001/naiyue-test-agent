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

    @staticmethod
    def _usage(connection):
        row=connection.execute("""SELECT count(*) AS retained,
            coalesce(sum(CASE WHEN lifecycle!='stopped' THEN 1 ELSE 0 END),0) AS running
            FROM execution_environments WHERE lifecycle!='destroyed'""").fetchone()
        return dict(row)

    def usage(self):
        with self.database.transaction() as connection:return self._usage(connection)

    def reserve(self, record, *, limits=None):
        try:
            with self.database.transaction(write=True) as connection:
                if limits is not None:
                    usage=self._usage(connection)
                    if usage['retained']>=limits.max_retained:raise SandboxError('env_retained_capacity')
                    if usage['running']>=limits.max_running:raise SandboxError('env_running_capacity')
                connection.execute('INSERT INTO execution_environments VALUES(?,?,?,?,?)',
                    (record.binding_id,record.project_id,record.session_id,record.lifecycle,
                     json.dumps(asdict(record),sort_keys=True)))
        except sqlite3.IntegrityError as error:
            raise SandboxError('env_already_exists') from error

    def reserve_start(self, binding_id, *, limits):
        with self.database.transaction(write=True) as connection:
            stored=connection.execute('SELECT record_json FROM execution_environments WHERE binding_id=?',(binding_id,)).fetchone()
            if stored is None:raise SandboxError('env_not_registered')
            row=_record(stored)
            if row.lifecycle=='destroyed':raise SandboxError('env_expired')
            if row.lifecycle=='stopped' and self._usage(connection)['running']>=limits.max_running:
                raise SandboxError('env_running_capacity')
            row=row.changed(lifecycle='starting')
            connection.execute('UPDATE execution_environments SET lifecycle=?,record_json=? WHERE binding_id=?',
                (row.lifecycle,json.dumps(asdict(row),sort_keys=True),binding_id))
            return row

    def get(self, binding_id):
        rows=self.database.read_rows('SELECT record_json FROM execution_environments WHERE binding_id=?',(binding_id,))
        if not rows: raise SandboxError('env_not_registered')
        return _record(rows[0])

    def list(self, project_id):
        return tuple(_record(row) for row in self.database.read_rows(
            'SELECT record_json FROM execution_environments WHERE project_id=? ORDER BY rowid',(project_id,)))

    def list_all(self):
        return tuple(_record(row) for row in self.database.read_rows('SELECT record_json FROM execution_environments ORDER BY rowid'))

    def touch(self, binding_id, now):
        with self.database.transaction(write=True) as connection:
            return connection.execute("""UPDATE execution_environments SET record_json=json_set(record_json,'$.last_used_at',
                max(coalesce(json_extract(record_json,'$.last_used_at'),?),?))
                WHERE binding_id=? AND lifecycle!='destroyed'""",(now,now,binding_id)).rowcount==1

    def save(self, record):
        with self.database.transaction(write=True) as connection:
            changed=connection.execute('''UPDATE execution_environments SET lifecycle=?,record_json=?
                WHERE binding_id=? AND project_id=? AND session_id=?''',
                (record.lifecycle,json.dumps(asdict(record),sort_keys=True),record.binding_id,record.project_id,record.session_id))
            if changed.rowcount!=1: raise SandboxError('env_not_registered')
