"""Atomic stopped-task upgrade reservations, backup binding and pin changes."""
import time
from pydantic import TypeAdapter
from naiyue_test_agent.contracts.cloud_releases import UpgradeTask,UpgradeBackup,UpgradeCancellation
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CloudTask,CommandReceipt,TaskId
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.storage.cloud.releases import meta,require_available
from .releases import ReleaseRepository




def _task(db,task_id):
    row=db.execute('SELECT record_json FROM tasks WHERE task_id=?',(task_id,)).fetchone()
    if row is None:raise CloudError('task_not_found',404)
    return CloudTask.model_validate_json(row[0])


def _receipt(db,ident):
    row=db.execute('SELECT record_json FROM commands WHERE command_id=?',(ident,)).fetchone()
    if row is None:raise CloudError('command_not_found',404)
    return CommandReceipt.model_validate_json(row[0])


class UpgradeRepository:
    def __init__(self,database,templates,max_receipts,instance_id):
        self.database,self.templates,self.instance_id=database,templates,instance_id
        self.receipts=ReleaseRepository(database,max_receipts)


    def reserve(self,task_id,value):
        task_id=TypeAdapter(TaskId).validate_python(task_id);request=UpgradeTask.model_validate(value)
        fingerprint=digest(dict(task_id=task_id,request=request.model_dump(),operation='release:upgrade'))
        def run(db):
            require_available(db);task=_task(db,task_id)
            if (task.revision,task.generation)!=(request.expected_revision,request.expected_generation):raise CloudError('task_revision_conflict')
            if (task.lifecycle!='stopped' or task.environment_phase!='absent' or task.sandbox_id or task.active_run_id
                    or task.environment_operation_id or task.control_operation_id or task.artifact_operation_id or task.reclaim_blockers):
                raise CloudError('task_upgrade_requires_stop')
            target=meta(db,'release:'+request.release_id+':'+task.template_id)
            if target is None:raise CloudError('release_not_ready')
            if target['release']['state_schema']!=task.state_schema:raise CloudError('upgrade_schema_incompatible')
            source=task.template_snapshot or self.templates.get(task.template_id)
            if source is None:raise CloudError('template_unavailable')
            reservation=dict(command_id=request.command_id,task_id=task_id,generation=task.generation,instance_id=self.instance_id,
                project_id=task.project_id,input_id=task.input_id,
                source_release_id=task.runner_release_id,source_template_digest=source.fingerprint,
                target_release_id=request.release_id,target_digest=digest(target),state_schema=task.state_schema)
            now=time.time();result=dict(phase='reserved',reservation=reservation,target_template=target['template'])
            candidate=task.model_copy(update={'revision':task.revision+1,'environment_operation_id':request.command_id,'updated_at':now})
            if db.execute('UPDATE tasks SET revision=?,record_json=? WHERE task_id=? AND revision=? AND generation=?',
                    (candidate.revision,candidate.model_dump_json(),task_id,task.revision,task.generation)).rowcount!=1:
                raise CloudError('task_revision_conflict')
            return CommandReceipt(command_id=request.command_id,resource_id=task_id,operation='release:upgrade',request_digest=fingerprint,
                expected_generation=task.generation,status='accepted',http_status=202,result=result,created_at=now,updated_at=now)
        return self.receipts.transact(request.command_id,fingerprint,run)



    def commit(self,task_id,value):
        request=UpgradeBackup.model_validate(value)
        if task_id!=request.task_id:raise CloudError('upgrade_backup_identity_mismatch')
        with self.database.transaction(write=True) as db:
            receipt=_receipt(db,request.command_id);result=receipt.result
            if receipt.operation!='release:upgrade' or receipt.resource_id!=task_id:raise CloudError('upgrade_backup_identity_mismatch')
            reservation=result['reservation']
            if (request.reservation_sha256!=digest(reservation) or request.generation!=reservation['generation']):
                raise CloudError('upgrade_backup_identity_mismatch')
            if result['phase']=='committed':
                if result['backup']!=request.model_dump():raise CloudError('upgrade_backup_identity_mismatch')
                return result
            if result['phase']!='verified':raise CloudError('upgrade_not_prepared')
            task=_task(db,task_id)
            source=task.template_snapshot or self.templates.get(task.template_id)
            if (task.environment_operation_id!=request.command_id or task.generation!=request.generation or task.sandbox_id
                    or task.lifecycle!='stopped' or source is None or source.fingerprint!=reservation['source_template_digest']):
                raise CloudError('task_revision_conflict')
            target=meta(db,'release:'+reservation['target_release_id']+':'+task.template_id)
            if target is None or digest(target)!=reservation['target_digest']:raise CloudError('release_identity_conflict')
            from naiyue_test_agent.contracts.cloud_tasks import TemplateReceipt
            template=TemplateReceipt.model_validate(target['template']);now=time.time()
            candidate=CloudTask.model_validate(task.model_dump()|dict(runner_release_id=template.runner_release_id,
                template_snapshot=template,template_digest=template.fingerprint,environment_operation_id=None,
                runner_boot_id=None,runner_connection_id=None,runner_phase='unconfigured',runner_model_ids=[],runner_capabilities=[],
                revision=task.revision+1,updated_at=now))
            result=result|dict(phase='committed',backup=request.model_dump())
            final=receipt.model_copy(update={'status':'succeeded','http_status':200,'updated_at':now,'result':result})
            if db.execute('UPDATE tasks SET revision=?,record_json=? WHERE task_id=? AND revision=? AND generation=?',
                    (candidate.revision,candidate.model_dump_json(),task_id,task.revision,task.generation)).rowcount!=1:raise CloudError('task_revision_conflict')
            db.execute('UPDATE commands SET record_json=? WHERE command_id=?',(final.model_dump_json(),request.command_id))
            return result


    def cancel(self,task_id,value):
        request=UpgradeCancellation.model_validate(value);command_id=request.command_id
        with self.database.transaction(write=True) as db:
            receipt=_receipt(db,command_id);task=_task(db,task_id)
            if receipt.operation!='release:upgrade' or receipt.resource_id!=task_id:raise CloudError('command_id_conflict')
            if (request.task_id!=task_id or request.generation!=receipt.result['reservation']['generation']
                    or request.reservation_sha256!=digest(receipt.result['reservation'])
                    or request.container_name!='nyt-upgrade-'+command_id.replace('-','')):
                raise CloudError('upgrade_backup_identity_mismatch')
            if receipt.result['phase']=='cancelled':return receipt.result
            if receipt.result['phase']=='committed':raise CloudError('upgrade_already_committed')
            if task.environment_operation_id!=command_id or task.generation!=request.generation:raise CloudError('task_revision_conflict')
            now=time.time();candidate=task.model_copy(update={'revision':task.revision+1,'environment_operation_id':None,'updated_at':now})
            result=receipt.result|dict(phase='cancelled',cleanup=request.model_dump())
            db.execute('UPDATE tasks SET revision=?,record_json=? WHERE task_id=? AND revision=?',
                (candidate.revision,candidate.model_dump_json(),task_id,task.revision))
            db.execute('UPDATE commands SET record_json=? WHERE command_id=?',
                (receipt.model_copy(update={'status':'failed','http_status':200,'error_code':'upgrade_cancelled','updated_at':now,'result':result}).model_dump_json(),command_id))
            return result

    def verified(self,task_id,command_id):
        with self.database.transaction(write=True) as db:
            receipt=_receipt(db,command_id);task=_task(db,task_id)
            if task.environment_operation_id!=command_id:raise CloudError('task_revision_conflict')
            value=receipt.result|dict(phase='verified')
            candidate=receipt.model_copy(update={'result':value,'updated_at':time.time()})
            db.execute('UPDATE commands SET record_json=? WHERE command_id=?',(candidate.model_dump_json(),command_id))
            return value
