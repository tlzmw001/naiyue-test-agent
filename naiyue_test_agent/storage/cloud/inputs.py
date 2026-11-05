"""Input and transfer indexes in the trusted registry, with atomic receipts."""
from .releases import require_available,active_template
from naiyue_test_agent.contracts.cloud_inputs import InputPackage,InputTransfer
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CommandReceipt,CloudTask


class InputRepository:
    def __init__(self,database):self.database=database

    def transfer(self,ident):
        with self.database.transaction() as c:row=c.execute('SELECT value FROM meta WHERE key=?',('transfer:'+ident,)).fetchone()
        if row is None:raise CloudError('transfer_not_found',404)
        return InputTransfer.model_validate_json(row[0])

    def transfers(self):
        with self.database.transaction() as c:
            return [InputTransfer.model_validate_json(row[0]) for row in c.execute(
                'SELECT value FROM meta WHERE key LIKE ?',('transfer:%',))]

    def input(self,ident):
        with self.database.transaction() as c:row=c.execute('SELECT value FROM meta WHERE key=?',('input:'+ident,)).fetchone()
        if row is None:raise CloudError('input_not_found',404)
        return InputPackage.model_validate_json(row[0])

    def create(self,transfer,receipt,limits,max_receipts):
        with self.database.transaction(write=True) as c:
            old=c.execute('SELECT record_json FROM commands WHERE command_id=?',(receipt.command_id,)).fetchone()
            if old is not None:
                previous=CommandReceipt.model_validate_json(old[0])
                if previous.request_digest!=receipt.request_digest:raise CloudError('command_id_conflict')
                return previous
            require_available(c)
            if c.execute('SELECT COUNT(*) FROM commands').fetchone()[0]>=max_receipts:raise CloudError('command_capacity')
            existing=c.execute('SELECT value FROM meta WHERE key=?',('input:'+transfer.input_id,)).fetchone()
            if existing is not None:
                transfer=transfer.model_copy(update={'cache_hit':True,'state':'committed','offset':transfer.size})
                receipt=receipt.model_copy(update={'result':{'transfer':transfer.model_dump()}})
            else:
                pending=[InputTransfer.model_validate_json(r[0]) for r in c.execute('SELECT value FROM meta WHERE key LIKE ?',('transfer:%',))]
                pending=[t for t in pending if t.state not in ('committed','cancelled')]
                used=sum(InputPackage.model_validate_json(r[0]).size for r in c.execute('SELECT value FROM meta WHERE key LIKE ?',('input:%',)))
                if len(pending)>=limits.max_pending or used+sum(t.size for t in pending)+transfer.size>limits.max_cache_bytes:
                    raise CloudError('input_capacity')
            c.execute('INSERT INTO meta(key,value) VALUES(?,?)',('transfer:'+transfer.transfer_id,transfer.model_dump_json()))
            c.execute('INSERT INTO commands(command_id,request_digest,resource_id,record_json) VALUES(?,?,?,?)',
                (receipt.command_id,receipt.request_digest,receipt.resource_id,receipt.model_dump_json()))
            return receipt

    def save(self,previous,replacement):
        with self.database.transaction(write=True) as c:
            if c.execute('UPDATE meta SET value=? WHERE key=? AND value=?',
                    (replacement.model_dump_json(),'transfer:'+previous.transfer_id,previous.model_dump_json())).rowcount!=1:
                raise CloudError('transfer_state_conflict')
        return replacement

    def commit(self,previous,package):
        replacement=previous.model_copy(update={'state':'committed','offset':previous.size})
        with self.database.transaction(write=True) as c:
            c.execute('INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)',('input:'+package.input_id,package.model_dump_json(by_alias=True)))
            if c.execute('UPDATE meta SET value=? WHERE key=? AND value=?',
                    (replacement.model_dump_json(),'transfer:'+previous.transfer_id,previous.model_dump_json())).rowcount!=1:
                raise CloudError('transfer_state_conflict')
        return package

    def create_task(self,task,receipt,max_receipts,max_tasks):
        with self.database.transaction(write=True) as c:
            old=c.execute('SELECT record_json FROM commands WHERE command_id=?',(receipt.command_id,)).fetchone()
            if old is not None:
                previous=CommandReceipt.model_validate_json(old[0])
                if previous.request_digest!=receipt.request_digest:raise CloudError('command_id_conflict')
                return previous
            if c.execute('SELECT 1 FROM projects WHERE project_id=?',(task.project_id,)).fetchone() is None:raise CloudError('project_not_found',404)
            if task.input_id is not None and c.execute('SELECT 1 FROM meta WHERE key=?',('input:'+task.input_id,)).fetchone() is None:raise CloudError('input_not_ready')
            require_available(c)
            selected=active_template(c,task.template_id)
            if selected is not None:
                from naiyue_test_agent.contracts.cloud_tasks import TemplateReceipt
                t=TemplateReceipt.model_validate(selected)
                task=CloudTask.model_validate(task.model_dump()|dict(runner_release_id=t.runner_release_id,template_snapshot=t,template_digest=t.fingerprint))
                receipt=receipt.model_copy(update={'result':{'task':task.model_dump()}})
            if task.input_id is None and (task.template_snapshot is None or task.template_snapshot.file_transfer_version!=1):
                raise CloudError('template_files_update_required')
            if c.execute('SELECT COUNT(*) FROM commands').fetchone()[0]>=max_receipts:raise CloudError('command_capacity')
            tasks=[CloudTask.model_validate_json(r[0]) for r in c.execute('SELECT record_json FROM tasks')]
            if sum(t.lifecycle!='deleted' for t in tasks)>=max_tasks:raise CloudError('environment_retained_capacity')
            c.execute('INSERT INTO tasks(task_id,project_id,generation,revision,record_json) VALUES(?,?,?,?,?)',
                (task.task_id,task.project_id,task.generation,task.revision,task.model_dump_json()))
            c.execute('INSERT INTO commands(command_id,request_digest,resource_id,record_json) VALUES(?,?,?,?)',
                (receipt.command_id,receipt.request_digest,receipt.resource_id,receipt.model_dump_json()))
        return receipt

    def attach(self,task_id,request):
        from naiyue_test_agent.contracts.cloud_tasks import TaskAttachment
        import time
        with self.database.transaction(write=True) as c:
            require_available(c)
            row=c.execute('SELECT record_json FROM tasks WHERE task_id=?',(task_id,)).fetchone()
            if row is None:raise CloudError('task_not_found',404)
            task=CloudTask.model_validate_json(row[0])
            if task.lifecycle in ('deleted','deleting'):raise CloudError('task_not_available')
            if task.template_snapshot is None or task.template_snapshot.file_transfer_version!=1:
                raise CloudError('template_files_update_required')
            package=c.execute('SELECT value FROM meta WHERE key=?',('input:'+request.input_id,)).fetchone()
            if package is None:raise CloudError('input_not_found',404)
            package=InputPackage.model_validate_json(package[0])
            if package.manifest.schema_version!=3:raise CloudError('attachment_format_required',422)
            value=TaskAttachment(**request.model_dump(),size=package.size)
            old=task.attachments.get(request.attachment_id)
            if old is not None:
                if old!=value:raise CloudError('attachment_identity_conflict')
                return old
            if len(task.attachments)>=1024:raise CloudError('attachment_capacity')
            updated=CloudTask.model_validate(task.model_dump()|dict(attachments=task.attachments|{value.attachment_id:value},
                revision=task.revision+1,updated_at=time.time()))
            c.execute('UPDATE tasks SET revision=?,record_json=? WHERE task_id=?',
                (updated.revision,updated.model_dump_json(),task_id))
            return value
