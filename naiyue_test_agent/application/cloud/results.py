"""Coordinator result identities and CAS; only trusted registry/artifact bytes are read."""
import time
from uuid import uuid4
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CommandReceipt
from naiyue_test_agent.contracts.cloud_results import ResultPlan,ResultBoundary,ResultRecord,ResultAction
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.storage.cloud.lifecycle import LifecycleRepository


class CloudResults:
    def __init__(self,runtime):
        self.runtime=runtime;self.repository=LifecycleRepository(runtime.database);self.closing=False

    def reserve_run(self,task,command_id,run_id):
        if task.pending_result_id or len(task.result_ids)>=self.runtime.settings.result_limits.max_results:
            raise CloudError('result_capacity')
        output=self.runtime.settings.result_limits.output_directory
        if any(e.path==output or e.path.startswith(output+'/') for e in self.runtime.inputs.get_input(task.input_id).manifest.entries):
            raise CloudError('result_output_conflict')
        plan=ResultPlan(result_id=str(uuid4()),source_command_id=command_id,source_run_id=run_id,task_id=task.task_id,
            project_id=task.project_id,input_id=task.input_id,source_generation=task.generation,
            output_directory=self.runtime.settings.result_limits.output_directory)
        now=time.time();record=ResultRecord(plan=plan,phase='waiting_terminal')
        receipt=CommandReceipt(command_id=plan.result_id,resource_id=task.task_id,operation='result:capture',
            expected_generation=task.generation,request_digest=digest(plan.model_dump()),status='accepted',http_status=202,
            result=record.model_dump(mode='json'),created_at=now,updated_at=now)
        return plan,receipt

    def _read(self,task_id,result_id):
        self.runtime.available_task(task_id)
        receipt=self.runtime.registry.get_command(result_id)
        if receipt.operation!='result:capture' or receipt.resource_id!=task_id:raise CloudError('result_not_found',404)
        return receipt,ResultRecord.model_validate(receipt.result)

    def get(self,task_id,result_id):
        with self.runtime.gates.hold(task_id):return self._read(task_id,result_id)[1]

    def terminal(self,candidate,run_receipt,boundary):
        raw=run_receipt.result['command'].get('result_plan')
        if raw is None:return candidate,()
        plan=ResultPlan.model_validate(raw);receipt,record=self._read(candidate.task_id,plan.result_id)
        if record.plan!=plan:raise CloudError('result_identity_conflict')
        if record.phase!='waiting_terminal':return candidate,()
        record=record.model_copy(update={'phase':'pending','boundary':ResultBoundary.model_validate(boundary),'revision':record.revision+1})
        receipt=receipt.model_copy(update={'result':record.model_dump(mode='json'),'updated_at':time.time()})
        candidate=candidate.model_copy(update={'pending_result_id':plan.result_id,'last_quiescent_at':None,
            'reclaim_blockers':list(dict.fromkeys([*candidate.reclaim_blockers,'result_pending']))})
        return candidate,(receipt,)

    def list(self,task_id,*,offset=0,limit=20):
        if not 0<=offset<=1000000 or not 1<=limit<=100:raise CloudError('invalid_result_page',422)
        with self.runtime.gates.hold(task_id):
            self.runtime.available_task(task_id)
            rows=self.runtime.registry.result_commands(task_id,offset=offset,limit=limit)
            result=[]
            for receipt in rows:
                record=ResultRecord.model_validate(receipt.result)
                result.append(dict(result_id=record.plan.result_id,source_run_id=record.plan.source_run_id,phase=record.phase,
                    revision=record.revision,outcome=record.boundary.outcome if record.boundary else None,error_code=record.error_code,
                    captured_at=record.bundle.captured_at if record.bundle else None,files_count=len(record.bundle.files) if record.bundle else None))
            return result

    def action(self,task_id,result_id,action,value):
        request=ResultAction.model_validate(value)
        fingerprint=digest(dict(task_id=task_id,result_id=result_id,action=action,request=request.model_dump()))
        with self.runtime.gates.hold(task_id):
            try:old=self.runtime.registry.get_command(request.command_id)
            except CloudError as error:
                if error.code!='command_not_found':raise
            else:
                if old.request_digest!=fingerprint:raise CloudError('command_id_conflict')
                return old
            task=self.runtime.available_task(task_id);receipt,record=self._read(task_id,result_id)
            if record.revision!=request.expected_revision:raise CloudError('result_revision_conflict')
            if task.pending_result_id!=result_id or task.artifact_operation_id:raise CloudError('result_busy')
            if record.phase not in ('pending','failed','unknown'):raise CloudError('result_not_retryable')
            if action not in ('retry','abandon'):raise CloudError('invalid_request',422)
            now=time.time();record=record.model_copy(update={'phase':'abandoned' if action=='abandon' else 'pending',
                'revision':record.revision+1,'attempt':record.attempt+(action=='retry'),'error_code':None})
            candidate=task.model_copy(update={'revision':task.revision+1,'updated_at':now,
                'pending_result_id':None if action=='abandon' else result_id,
                'reclaim_blockers':[v for v in task.reclaim_blockers if v!='result_pending'] if action=='abandon' else task.reclaim_blockers})
            if action=='abandon' and not candidate.reclaim_blockers and candidate.runner_phase=='ready':
                candidate=candidate.model_copy(update={'last_quiescent_at':now})
            updated=receipt.model_copy(update={'status':'failed' if action=='abandon' else 'accepted','http_status':200,
                'error_code':'result_abandoned' if action=='abandon' else None,'updated_at':now,'result':record.model_dump(mode='json')})
            result=CommandReceipt(command_id=request.command_id,resource_id=task_id,operation='result:'+action,
                expected_generation=task.generation,request_digest=fingerprint,status='succeeded',http_status=200,
                result={'result_id':result_id,'revision':record.revision},created_at=now,updated_at=now)
            # An action receipt, updated result and its task fence commit together.
            self.repository.reserve(task,candidate,result,self.runtime.settings,related_updates=(updated,))
            return result

    def begin(self,raw,task_id,result_id):
        r=self.runtime
        with r.task_io.hold(task_id),r.gates.hold(task_id):
            task=r.credentials.authorize(raw,task_id,'artifacts:write');receipt,record=self._read(task_id,result_id)
            if record.phase in ('available','capturing','verifying'):return record
            if (record.phase!='pending' or task.pending_result_id!=result_id or task.active_run_id or task.environment_operation_id
                    or task.control_operation_id or task.artifact_operation_id
                    or any(v not in ('result_pending','recovery_required') for v in task.reclaim_blockers)):
                raise CloudError('result_not_quiescent')
            record=record.model_copy(update={'phase':'verifying' if record.bundle else 'capturing','revision':record.revision+1,
                'generation':record.generation or task.generation,'error_code':None})
            candidate=task.model_copy(update={'revision':task.revision+1,'artifact_operation_id':result_id,
                'last_quiescent_at':None,'reclaim_blockers':list(dict.fromkeys([*task.reclaim_blockers,'result_capture']))})
            self.repository.finish(task,candidate,receipt.model_copy(update={'status':'running','http_status':202,
                'expected_generation':record.generation,'updated_at':time.time(),'result':record.model_dump(mode='json')}))
            return record

    def prepare(self,raw,task_id,result_id,value):
        from naiyue_test_agent.contracts.cloud_results import ResultBundle
        bundle=ResultBundle.model_validate(value);r=self.runtime;limits=r.settings.result_limits
        with r.task_io.hold(task_id),r.gates.hold(task_id):
            task=r.credentials.authorize(raw,task_id,'artifacts:write');receipt,record=self._read(task_id,result_id)
            if record.bundle is not None:
                if record.bundle!=bundle:raise CloudError('result_identity_conflict')
                return record
            if (record.phase!='capturing' or task.pending_result_id!=result_id or task.artifact_operation_id!=result_id
                    or bundle.plan!=record.plan or bundle.boundary!=record.boundary or bundle.generation!=record.generation):
                raise CloudError('result_identity_conflict')
            if len(bundle.files)>limits.max_artifacts+2 or sum(f.artifact.size for f in bundle.files)>limits.max_result_bytes:
                raise CloudError('result_byte_budget',413)
            if len(bundle.model_dump_json().encode())>limits.max_metadata_bytes:raise CloudError('result_metadata_budget',413)
            for file in bundle.files:
                if file.role=='patch' and file.artifact.size>limits.max_patch_bytes:raise CloudError('result_patch_budget',413)
                if file.role=='manifest' and file.artifact.size>limits.max_metadata_bytes:raise CloudError('result_metadata_budget',413)
                if file.artifact.size>r.settings.artifact_limits.max_file_bytes:raise CloudError('artifact_budget',413)
                if file.role in ('manifest','patch') and file.artifact.relative_name!={'manifest':'output-manifest.json','patch':'changes.patch'}[file.role]:
                    raise CloudError('result_slot_invalid')
                try:old=r.artifacts.repository.get(task_id,file.artifact.artifact_id)
                except CloudError as error:
                    if error.code!='artifact_not_found':raise
                else:
                    if old.artifact!=file.artifact:raise CloudError('result_slot_invalid')
            record=record.model_copy(update={'bundle':bundle,'phase':'verifying','revision':record.revision+1})
            candidate=task.model_copy(update={'revision':task.revision+1,'updated_at':time.time()})
            self.repository.finish(task,candidate,receipt.model_copy(update={'result':record.model_dump(mode='json'),'updated_at':time.time()}))
            return record

    def authorize_artifact(self,task,record,receipt):
        result=ResultRecord.model_validate(receipt.result)
        if (receipt.resource_id!=task.task_id or receipt.command_id!=record.command_id or result.bundle is None
                or result.phase not in ('verifying','available') or result.plan.task_id!=task.task_id
                or result.plan.input_id!=task.input_id or record.generation!=result.bundle.generation
                or record.generation>task.generation or result.phase!='available' and task.artifact_operation_id!=record.command_id
                or not any(f.artifact==record for f in result.bundle.files)):
            raise CloudError('result_slot_invalid')
        if result.phase=='available':
            stored=self.runtime.artifacts.repository.get(task.task_id,record.artifact_id)
            if stored.state!='available' or stored.artifact!=record:raise CloudError('result_slot_invalid')
        return task

    def commit(self,raw,task_id,result_id):
        from naiyue_test_agent.contracts.cloud_results import OutputManifest
        from naiyue_test_agent.cloud_files.archive import strict_json
        from naiyue_test_agent.cloud_files.paths import check_links
        r=self.runtime
        with r.task_io.hold(task_id):
            with r.gates.hold(task_id):
                task=r.credentials.authorize(raw,task_id,'artifacts:write');receipt,record=self._read(task_id,result_id)
                if record.phase=='available':return record
                if record.phase!='verifying' or record.bundle is None or task.artifact_operation_id!=result_id:raise CloudError('result_not_uploaded')
                for file in record.bundle.files:r.artifacts._get(task_id,file.artifact.artifact_id)
                manifest_file=next(f.artifact for f in record.bundle.files if f.role=='manifest')
                transfer=r.artifacts.repository.get(task_id,manifest_file.artifact_id)
            with r.artifacts.store.open(transfer) as stream:
                data=stream.read(r.settings.result_limits.max_metadata_bytes+1)
            if len(data)>r.settings.result_limits.max_metadata_bytes:raise CloudError('result_metadata_budget')
            manifest=OutputManifest.model_validate(strict_json(data))
            original=r.inputs.get_input(record.plan.input_id)
            if (manifest.canonical!=data or manifest.input_id!=record.plan.input_id
                    or manifest.policy_sha256!=original.manifest.selection['policy_sha256']):raise CloudError('result_manifest_mismatch')
            if (len(manifest.entries)>r.settings.result_limits.max_files
                    or sum(e.size for e in manifest.entries)>r.settings.result_limits.max_result_bytes):raise CloudError('result_byte_budget')
            check_links(manifest.entries)
            with r.gates.hold(task_id):
                task=r.credentials.authorize(raw,task_id,'artifacts:write');current,state=self._read(task_id,result_id)
                if state.phase=='available':return state
                if state!=record or task.artifact_operation_id!=result_id or task.pending_result_id!=result_id:raise CloudError('result_identity_conflict')
                state=state.model_copy(update={'phase':'available','revision':state.revision+1,'error_code':None})
                blockers=[v for v in task.reclaim_blockers if v not in ('result_pending','result_capture','result_unknown')]
                candidate=task.model_copy(update={'revision':task.revision+1,'pending_result_id':None,'artifact_operation_id':None,
                    'reclaim_blockers':blockers,'last_quiescent_at':time.time() if not blockers and task.runner_phase=='ready' else None})
                self.repository.finish(task,candidate,current.model_copy(update={'status':'succeeded','http_status':200,
                    'result':state.model_dump(mode='json'),'error_code':None,'updated_at':time.time()}))
                return state

    def failed(self,raw,task_id,result_id,value):
        import re
        code=value.get('error_code')
        if set(value)!={'error_code'} or not isinstance(code,str) or not re.fullmatch('[a-z_]{1,128}',code):raise CloudError('invalid_request',422)
        r=self.runtime
        with r.task_io.hold(task_id),r.gates.hold(task_id):
            task=r.credentials.authorize(raw,task_id,'artifacts:write');receipt,record=self._read(task_id,result_id)
            if record.phase in ('available','abandoned','failed'):return record
            if task.artifact_operation_id!=result_id or task.pending_result_id!=result_id:raise CloudError('result_identity_conflict')
            record=record.model_copy(update={'phase':'failed','revision':record.revision+1,'error_code':code})
            candidate=task.model_copy(update={'revision':task.revision+1,'artifact_operation_id':None,
                'reclaim_blockers':[v for v in task.reclaim_blockers if v!='result_capture']})
            self.repository.finish(task,candidate,receipt.model_copy(update={'status':'failed','http_status':200,
                'result':record.model_dump(mode='json'),'error_code':code,'updated_at':time.time()}))
            return record
