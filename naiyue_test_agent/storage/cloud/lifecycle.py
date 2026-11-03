"""Atomic environment reservations and final facts in the Coordinator registry."""
from naiyue_test_agent.contracts.cloud_tasks import CloudError, CloudTask, CommandReceipt


def active(task):
    return bool(task.sandbox_id or task.environment_operation_id or task.environment_phase == 'unknown')


class LifecycleRepository:
    def __init__(self, database):
        self.database = database

    def task_page(self, after, limit):
        with self.database.transaction() as connection:
            return [CloudTask.model_validate_json(r[0]) for r in connection.execute(
                'SELECT record_json FROM tasks WHERE task_id>? ORDER BY task_id LIMIT ?', (after, limit))]

    def reserve(self, previous, candidate, receipt, settings, *, related=(), related_updates=()):
        with self.database.transaction(write=True) as connection:
            old = connection.execute('SELECT record_json FROM commands WHERE command_id=?', (receipt.command_id,)).fetchone()
            if old is not None:
                found = CommandReceipt.model_validate_json(old[0])
                if found.request_digest != receipt.request_digest: raise CloudError('command_id_conflict')
                return found, False
            if connection.execute('SELECT COUNT(*) FROM commands').fetchone()[0]+1+len(related) > settings.max_receipts:
                raise CloudError('command_capacity')
            tasks = [CloudTask.model_validate_json(r[0]) for r in connection.execute('SELECT record_json FROM tasks')]
            starting = receipt.operation == 'environment:start'
            if starting and len([t for t in tasks if t.lifecycle != 'deleted']) > settings.max_retained_environments:
                raise CloudError('environment_retained_capacity')
            if starting and not active(previous) and sum(active(t) for t in tasks) >= settings.max_active_environments:
                raise CloudError('environment_capacity')
            changed = connection.execute('''UPDATE tasks SET generation=?,revision=?,record_json=?
                WHERE task_id=? AND revision=? AND generation=?''', (candidate.generation, candidate.revision,
                candidate.model_dump_json(), previous.task_id, previous.revision, previous.generation)).rowcount
            if changed != 1: raise CloudError('task_revision_conflict')
            if candidate.generation != previous.generation:
                connection.execute('UPDATE task_credentials SET revoked_at=? WHERE task_id=? AND revoked_at IS NULL',
                    (candidate.updated_at, candidate.task_id))
            connection.execute('INSERT INTO commands(command_id,request_digest,resource_id,record_json) VALUES(?,?,?,?)',
                (receipt.command_id, receipt.request_digest, receipt.resource_id, receipt.model_dump_json()))
            for item in related:
                connection.execute('INSERT INTO commands(command_id,request_digest,resource_id,record_json) VALUES(?,?,?,?)',
                    (item.command_id,item.request_digest,item.resource_id,item.model_dump_json()))
            for item in related_updates:
                if connection.execute('UPDATE commands SET record_json=? WHERE command_id=? AND request_digest=?',
                        (item.model_dump_json(),item.command_id,item.request_digest)).rowcount!=1:
                    raise CloudError('command_state_conflict')
        return receipt, True

    def save_receipt(self, receipt):
        with self.database.transaction(write=True) as connection:
            changed = connection.execute('UPDATE commands SET record_json=? WHERE command_id=? AND request_digest=?',
                (receipt.model_dump_json(), receipt.command_id, receipt.request_digest)).rowcount
            if changed != 1: raise CloudError('command_state_conflict')

    def finish(self, previous, candidate, receipt, *, related=(), retired_artifacts=()):
        with self.database.transaction(write=True) as connection:
            changed = connection.execute('''UPDATE tasks SET generation=?,revision=?,record_json=?
                WHERE task_id=? AND revision=? AND generation=?''', (candidate.generation, candidate.revision,
                candidate.model_dump_json(), candidate.task_id, previous.revision, previous.generation)).rowcount
            if changed != 1: raise CloudError('task_revision_conflict')
            changed = connection.execute('UPDATE commands SET record_json=? WHERE command_id=? AND request_digest=?',
                (receipt.model_dump_json(), receipt.command_id, receipt.request_digest)).rowcount
            if changed != 1: raise CloudError('command_state_conflict')
            for item in related:
                changed=connection.execute('UPDATE commands SET record_json=? WHERE command_id=? AND request_digest=?',
                    (item.model_dump_json(),item.command_id,item.request_digest)).rowcount
                if changed!=1:raise CloudError('command_state_conflict')
            for artifact_id in retired_artifacts:
                connection.execute('DELETE FROM meta WHERE key=?',('artifact:'+candidate.task_id+':'+artifact_id,))
            if receipt.operation=='result:capture' and receipt.result.get('phase')=='deleted':
                connection.execute('DELETE FROM meta WHERE key LIKE ?',('result-lease:'+candidate.task_id+':'+receipt.command_id+':%',))
