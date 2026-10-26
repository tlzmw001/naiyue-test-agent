"""Host delivery ownership and necessary saves, independent of envd mutations."""
import logging
from naiyue_test_agent.contracts.git_delivery import DeliveryCommand,GitDeliveryError
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.contracts.git_results import result_receipt
from naiyue_test_agent.execution.git_delivery import GitDelivery
from naiyue_test_agent.execution.git_repository import bundle_digest
from naiyue_test_agent.execution.git_results import ResultImporter
from naiyue_test_agent.sandbox_settings import GitInputSettings
from naiyue_test_agent.storage.files import check_store_directory
from naiyue_test_agent.storage.git_deliveries import GitDeliveries

logger=logging.getLogger(__name__)


class DeliveryService:
    def __init__(self,results,settings):self.results,self.settings=results,settings

    def records(self):return GitDeliveries(self.results.operations.records().database)

    def _driver(self,binding,ident):
        row=self.results._row(binding,ident);facts=row['metadata']['delivery']
        if facts['state']!='ready':raise GitDeliveryError('git_delivery_result_not_ready')
        receipt=result_receipt(facts['receipt'])
        env=self.results.environments.get(binding)
        record=self.results.inputs.get(row['project_id'],receipt['input_id'])
        if any(receipt[key]!=value for key,value in dict(project_id=row['project_id'],binding_id=binding,
                session_id=env.session_id,input_snapshot=record.snapshot.input_snapshot,
                original_head=record.snapshot.original_head,result_id=ident).items()):
            raise GitDeliveryError('git_delivery_binding_mismatch')
        project=self.results.inputs._project(row['project_id'])
        if not project.trusted:raise GitDeliveryError('project_trust_required')
        directory=self.results.directory(project.project_id,ident);check_store_directory(directory)
        if bundle_digest(directory/'result.bundle',record.settings['max_bundle_bytes'])!=(receipt['bundle_size'],receipt['bundle_sha256']):
            raise GitDeliveryError('git_delivery_corrupt')
        importer=ResultImporter(directory,GitInputSettings.model_validate(record.settings))
        importer.repository.run(['fsck','--strict','--no-reflogs'])
        return row,project,GitDelivery(importer,project.source,receipt,self.settings)

    @staticmethod
    def view(row):
        return {key:row[key] for key in ('operation_id','result_id','command','state','result','error_code','created_at')}

    def list(self,binding,ident):
        row=self.results._row(binding,ident)
        return [self.view(value) for value in self.records().list(row['project_id'],binding,ident)]

    def options(self,binding,ident):
        row,project,driver=self._driver(binding,ident)
        discarded=row['metadata']['delivery'].get('discarded',False)
        actions={name:dict(available=not discarded,reason='git_delivery_discarded' if discarded else None)
            for name in ('branch','discard')}
        try:
            if discarded:raise GitDeliveryError('git_delivery_discarded')
            driver.plan('merge');actions['merge']=dict(available=True,reason=None)
        except (GitDeliveryError,GitInputError) as error:actions['merge']=dict(available=False,reason=error.code)
        return dict(discarded=discarded,actions=actions,operations=self.list(binding,ident))

    def execute(self,binding,ident,value):
        command=DeliveryCommand.model_validate(value)
        with self.results.operations.accepting(),self.results.operations._lock(ident):
            capture=self.results._row(binding,ident);project_id=capture['project_id'];records=self.records()
            row,new=records.reserve(project_id,binding,ident,command,self.settings)
            if row['state'] in ('completed','acknowledged','failed'):return self.view(row)
            try:
                capture,project,driver=self._driver(binding,ident)
                if row['state'] in ('executing','unknown'):
                    if row['plan']['project_revision']!=project.revision:raise GitDeliveryError('git_delivery_project_changed')
                    result=driver.reconcile(row['plan'])
                    if result['state']=='completed':return self.view(records.complete(project_id,binding,command.operation_id,result))
                    return self.view(records.save(project_id,binding,command.operation_id,dict(state='unknown',result=result,error_code='git_delivery_unconfirmed')))
                if capture['metadata']['delivery'].get('discarded'):raise GitDeliveryError('git_delivery_discarded')
                plan=driver.plan(command.action,branch=command.branch)
                plan['project_revision']=project.revision
                row=records.save(project_id,binding,command.operation_id,dict(state='executing',plan=plan))
                if self.results.inputs._project(project_id).revision!=project.revision:raise GitDeliveryError('git_delivery_project_changed')
                def progress(oid):records.save(project_id,binding,command.operation_id,dict(last_commit=oid))
                result=driver.execute(plan,on_progress=progress)
                if self.results.inputs._project(project_id).revision!=project.revision:raise GitDeliveryError('git_delivery_project_changed')
                if result['state']!='completed':raise GitDeliveryError('git_delivery_unconfirmed')
                return self.view(records.complete(project_id,binding,command.operation_id,result))
            except (GitDeliveryError,GitInputError,OSError) as error:
                state='failed' if row['state']=='preparing' else 'unknown'
                code=error.code if hasattr(error,'code') else 'git_delivery_unconfirmed'
                logger.warning('git_delivery_failed action=%s state=%s code=%s',command.action,state,code)
                try:records.save(project_id,binding,command.operation_id,dict(state=state,error_code=code))
                except Exception as secondary:logger.error('git_delivery_failure_save_unconfirmed error_type=%s',type(secondary).__name__)
                raise GitDeliveryError(code) from error

    def acknowledge(self,binding,ident,operation):
        capture=self.results._row(binding,ident);record=self.records().get(capture['project_id'],binding,operation)
        if record['result_id']!=ident:raise GitDeliveryError('git_delivery_not_found')
        return self.view(self.records().acknowledge(capture['project_id'],binding,operation))
