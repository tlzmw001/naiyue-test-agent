"""Atomic deletion fence, phases and terminal receipt redaction in the registry."""
from naiyue_test_agent.contracts.cloud_tasks import CloudError, CommandReceipt


class DeletionRepository:
    def __init__(self, database): self.database = database

    def reserve(self, previous, candidate, receipt, settings):
        with self.database.transaction(write=True) as c:
            old=c.execute('SELECT record_json FROM commands WHERE command_id=?',(receipt.command_id,)).fetchone()
            if old is not None:
                found=CommandReceipt.model_validate_json(old[0])
                if found.request_digest!=receipt.request_digest or found.operation!='task:delete':raise CloudError('command_id_conflict')
                return found,False
            # One delete per retained task; bounded management headroom keeps cleanup
            # possible when ordinary receipts are full.
            from .releases import require_available
            require_available(c)
            if c.execute('SELECT COUNT(*) FROM commands').fetchone()[0] >= settings.max_receipts + settings.max_retained_environments:
                raise CloudError('command_capacity')
            changed = c.execute('UPDATE tasks SET revision=?,record_json=? WHERE task_id=? AND revision=? AND generation=?',
                (candidate.revision, candidate.model_dump_json(), previous.task_id, previous.revision, previous.generation)).rowcount
            if changed != 1: raise CloudError('task_revision_conflict')
            c.execute('INSERT INTO commands VALUES(?,?,?,?)', (receipt.command_id, receipt.request_digest,
                receipt.resource_id, receipt.model_dump_json()))
            c.execute('UPDATE task_credentials SET revoked_at=? WHERE task_id=? AND revoked_at IS NULL',
                (candidate.updated_at, candidate.task_id))
            return receipt,True

    def save(self, previous, candidate, receipt, *, complete=False):
        with self.database.transaction(write=True) as c:
            changed = c.execute('UPDATE tasks SET revision=?,record_json=? WHERE task_id=? AND revision=? AND generation=?',
                (candidate.revision, candidate.model_dump_json(), previous.task_id, previous.revision, previous.generation)).rowcount
            if changed != 1: raise CloudError('task_revision_conflict')
            if complete:
                c.execute('DELETE FROM meta WHERE key=? OR key LIKE ?',
                    ('history:' + candidate.task_id, 'artifact:' + candidate.task_id + ':%'))
                c.execute('DELETE FROM meta WHERE key=?',('mcp-task:'+candidate.task_id,))
                c.execute('DELETE FROM meta WHERE key LIKE ?',('result-lease:'+candidate.task_id+':%',))
                c.execute('DELETE FROM task_credentials WHERE task_id=?', (candidate.task_id,))
                for row in c.execute('SELECT record_json FROM commands WHERE resource_id=? AND command_id!=?',
                        (candidate.task_id, receipt.command_id)).fetchall():
                    old = CommandReceipt.model_validate_json(row[0])
                    old = old.model_copy(update=dict(status='failed', http_status=410, error_code='task_deleted',
                        result={'delete_command_id': receipt.command_id}, updated_at=receipt.updated_at))
                    c.execute('UPDATE commands SET record_json=? WHERE command_id=?', (old.model_dump_json(), old.command_id))
            changed = c.execute('UPDATE commands SET record_json=? WHERE command_id=? AND request_digest=?',
                (receipt.model_dump_json(), receipt.command_id, receipt.request_digest)).rowcount
            if changed != 1: raise CloudError('command_state_conflict')
