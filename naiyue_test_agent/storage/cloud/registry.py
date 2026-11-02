"""Trusted task metadata; no sandbox, checkpoint or Agent construction."""
from naiyue_test_agent.contracts.cloud_tasks import CloudError, CloudProject, CloudTask, CommandReceipt


class CloudRegistry:
    def __init__(self, database):
        self.database = database

    def list_projects(self):
        with self.database.transaction() as connection:
            return [CloudProject.model_validate_json(r[0]) for r in connection.execute(
                'SELECT record_json FROM projects ORDER BY project_id')]

    def bind_instance(self, instance_id, *, initialize):
        with self.database.transaction(write=initialize) as connection:
            row = connection.execute("SELECT value FROM meta WHERE key='instance_id'").fetchone()
            if row is None and not initialize:
                raise CloudError('cloud_instance_missing', 503)
            if row is not None and row[0] != instance_id:
                raise CloudError('cloud_instance_mismatch', 503)
            if initialize:
                connection.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('instance_id',?)", (instance_id,))

    def get_task(self, task_id):
        with self.database.transaction() as connection:
            row = connection.execute('SELECT record_json FROM tasks WHERE task_id=?', (task_id,)).fetchone()
        if row is None:
            raise CloudError('task_not_found', 404)
        return CloudTask.model_validate_json(row[0])

    def add_task(self, record):
        record = CloudTask.model_validate(record)
        with self.database.transaction(write=True) as connection:
            connection.execute('INSERT INTO tasks(task_id,project_id,generation,revision,record_json) VALUES(?,?,?,?,?)',
                (record.task_id, record.project_id, record.generation, record.revision, record.model_dump_json()))

    def get_command(self, command_id):
        with self.database.transaction() as connection:
            row = connection.execute('SELECT record_json FROM commands WHERE command_id=?', (command_id,)).fetchone()
        if row is None:
            raise CloudError('command_not_found', 404)
        return CommandReceipt.model_validate_json(row[0])

    def run_command(self,task_id,run_id):
        with self.database.transaction() as connection:
            rows=connection.execute('SELECT record_json FROM commands WHERE resource_id=?',(task_id,))
            matches=[]
            for row in rows:
                receipt=CommandReceipt.model_validate_json(row[0])
                if receipt.operation=='run:start' and receipt.result['command']['run_id']==run_id:matches.append(receipt)
            if len(matches)!=1:raise CloudError('run_receipt_missing')
            return matches[0]

    def history_status(self, task_id):
        with self.database.transaction() as connection:
            row = connection.execute('SELECT value FROM meta WHERE key=?', ('history:' + task_id,)).fetchone()
        if row is not None and row[0] not in ('initializing', 'ready'):
            raise CloudError('cloud_invalid_format', 503)
        return row[0] if row is not None else None

    def history_status_set(self, task_id, status):
        if status not in ('initializing', 'ready'):
            raise ValueError('invalid_history_status')
        with self.database.transaction(write=True) as connection:
            connection.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                ('history:' + task_id, status))

    def commit_project(self, project, receipt, *, max_receipts):
        with self.database.transaction(write=True) as connection:
            old = connection.execute('SELECT record_json FROM commands WHERE command_id=?',
                (receipt.command_id,)).fetchone()
            if old is not None:
                previous = CommandReceipt.model_validate_json(old[0])
                if previous.request_digest != receipt.request_digest:
                    raise CloudError('command_id_conflict')
                return previous
            if connection.execute('SELECT COUNT(*) FROM commands').fetchone()[0] >= max_receipts:
                raise CloudError('command_capacity')
            connection.execute('INSERT INTO projects(project_id,record_json) VALUES(?,?)',
                (project.project_id, project.model_dump_json()))
            connection.execute('INSERT INTO commands(command_id,request_digest,resource_id,record_json) VALUES(?,?,?,?)',
                (receipt.command_id, receipt.request_digest, receipt.resource_id, receipt.model_dump_json()))
        return receipt

    def save_task_cas(self, replacement, *, expected_revision, expected_generation, revoked_at):
        with self.database.transaction(write=True) as connection:
            changed = connection.execute('''UPDATE tasks SET generation=?,revision=?,record_json=?
                WHERE task_id=? AND revision=? AND generation=?''', (replacement.generation, replacement.revision,
                replacement.model_dump_json(), replacement.task_id, expected_revision, expected_generation)).rowcount
            if changed != 1:
                raise CloudError('task_revision_conflict')
            if replacement.generation != expected_generation:
                connection.execute('UPDATE task_credentials SET revoked_at=? WHERE task_id=? AND revoked_at IS NULL',
                    (revoked_at, replacement.task_id))

    def resource_counts(self):
        with self.database.transaction() as connection:
            row = connection.execute('''SELECT
                COALESCE(SUM(json_extract(record_json,'$.sandbox_id') IS NOT NULL
                    OR json_extract(record_json,'$.environment_operation_id') IS NOT NULL
                    OR json_extract(record_json,'$.environment_phase')='unknown'),0),
                COALESCE(SUM(json_extract(record_json,'$.environment_operation_id') IS NOT NULL),0),
                COALESCE(SUM(json_extract(record_json,'$.environment_phase')='unknown'
                    OR json_extract(record_json,'$.lifecycle')='reconciling'),0),
                COALESCE(SUM(json_extract(record_json,'$.lifecycle')!='deleted'),0)
                FROM tasks''').fetchone()
        return dict(zip(('active', 'reserved', 'unknown', 'retained'), row))
