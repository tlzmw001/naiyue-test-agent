"""Durable per-run plans drive result publication even while the local UI is offline."""
import os,logging
from pathlib import Path
from pydantic import TypeAdapter
from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.contracts.cloud_results import ResultPlan,ResultRecord
from naiyue_test_agent.contracts.cloud_tasks import CloudError,UUIDText
from naiyue_test_agent.application.cloud.ownership import private_directory
from .state import read_json,write_json
from .result_snapshot import freeze_result,verify_spool
from .http import task_request
from .artifact_transport import upload_record
from .result_cleanup import directory_identity,check_roots,cleanup_proof,remove_confirmed

logger=logging.getLogger(__name__)


class RunnerResults:
    def __init__(self,kernel,root,state):
        self.kernel,self.root,self.state=kernel,root,state;self.freezing=False
        self.directory=state/'result-plans';self.directory.mkdir(mode=0o700,exist_ok=True);private_directory(self.directory)
        self.spools=state/'results';self.spools.mkdir(mode=0o700,exist_ok=True);private_directory(self.spools)
        self.identities=dict(plans=directory_identity(self.directory.lstat()),spools=directory_identity(self.spools.lstat()))

    def path(self,rid):
        TypeAdapter(UUIDText).validate_python(rid)
        return self.directory/(rid+'.json')

    def admit(self,command):
        plan=command.result_plan
        if plan is None:return
        check_roots(self.directory,self.spools,self.identities)
        b=self.kernel.bootstrap
        if (plan.source_command_id!=command.command_id or plan.source_run_id!=command.run_id
                or any(getattr(plan,k)!=getattr(b,k) for k in ('task_id','project_id','input_id'))
                or plan.source_generation>b.generation):raise CloudError('result_identity_conflict')
        path=self.path(plan.result_id)
        if os.path.lexists(path):
            value=read_json(path)
            if ResultPlan.model_validate(value['plan'])!=plan:raise CloudError('result_identity_conflict')
            return
        # Coordinator may supersede the old result before this publisher can GET it.
        if sum(not value['done'] for value in self._plans())>=b.result_limits.max_results+1:raise CloudError('result_capacity')
        write_json(path,dict(plan=plan.model_dump(mode='json'),done=False,started_attempt=None))

    def retire(self,local):
        plan=ResultPlan.model_validate(local['plan'])
        try:
            b=self.kernel.bootstrap
            if (any(getattr(plan,k)!=getattr(b,k) for k in ('task_id','project_id','input_id')) or
                    plan.source_generation>b.generation):raise CloudError('result_identity_conflict')
            check_roots(self.directory,self.spools,self.identities)
            local['done']=True;write_json(self.path(plan.result_id),local)
            if 'cleanup' not in local:
                local['cleanup']=cleanup_proof(self.directory,self.spools,plan,self.kernel.bootstrap.result_limits,self.identities)
                write_json(self.path(plan.result_id),local)
            remove_confirmed(self.directory,self.spools,plan,local['cleanup'],self.identities)
        except (CloudError,OSError,ValueError) as error:
            logger.warning('runner_result_cleanup_failed code=%s',error.code if isinstance(error,CloudError) else 'result_cleanup_unavailable')

    def _plans(self):
        check_roots(self.directory,self.spools,self.identities)
        files=sorted(self.directory.iterdir())
        for path in files:
            if path.name.endswith('.json.next'):
                TypeAdapter(UUIDText).validate_python(path.name[:-len('.json.next')]);continue
            TypeAdapter(UUIDText).validate_python(path.stem)
            try:value=read_json(path)
            except FileNotFoundError:
                # Admission may read the publisher's inventory just after retirement.
                continue
            plan=ResultPlan.model_validate(value['plan'])
            if path!=self.path(plan.result_id):raise CloudError('result_identity_conflict')
            yield value

    def pending(self):
        result=[]
        for value in self._plans():
            if value['done']:self.retire(value)
            else:result.append(value)
        if len(result)>self.kernel.bootstrap.result_limits.max_results+1:raise CloudError('result_capacity')
        return result

    async def publish(self,client):
        for local in await run_blocking(self.pending):
            plan=ResultPlan.model_validate(local['plan']);b=client.bootstrap
            journal=await run_blocking(self.kernel.journal.lookup,plan.source_command_id)
            rejected=await run_blocking(self.kernel.journal.rejection,plan.source_command_id) if journal is None else None
            if journal is None and rejected is None:continue
            if journal and journal['status'] in ('running','unknown','interrupted') and journal['terminal_seq'] is None:continue
            path='/internal/cloud/v1/tasks/'+b.task_id+'/results/'+plan.result_id
            record=ResultRecord.model_validate(await task_request(client.http,b,'GET',path))
            if record.plan!=plan:raise CloudError('result_identity_conflict')
            if (record.phase=='superseded' and record.superseded_by
                    and not await run_blocking(self.kernel.journal.admitted_run,record.superseded_by)):continue
            if record.phase in ('available','abandoned','superseded','deleted'):
                await run_blocking(self.retire,local);continue
            if rejected is not None:continue
            if record.phase in ('waiting_terminal','failed','unknown','deleting'):continue
            if not self.kernel.session_quiet():continue
            # Quiet facts need not wait for the next periodic heartbeat.
            await client.send(dict(kind='heartbeat',quiescent=True,phase=self.kernel.phase,run_id=self.kernel.last_run_id,
                recovery=self.kernel.recovery_info.model_dump() if self.kernel.recovery_info else None))
            try:
                record=ResultRecord.model_validate(await task_request(client.http,b,'POST',path+'/begin',value={}))
            except CloudError as error:
                if error.code in ('result_not_quiescent','result_busy'):continue
                raise
            if record.plan!=plan:raise CloudError('result_identity_conflict')
            if record.phase in ('available','abandoned','superseded','deleted'):
                await run_blocking(self.retire,local);continue
            if record.boundary and (record.boundary.epoch!=b.state_epoch or record.boundary.kind=='run'
                    and (record.boundary.seq!=journal['terminal_seq'] or record.boundary.outcome!=journal['status'] and not (journal['status']=='recovered' and record.boundary.outcome in ('failed','interrupted')))):
                raise CloudError('result_boundary_conflict')
            if record.boundary is None or record.generation is None:raise CloudError('result_identity_conflict')
            spool=self.spools/plan.result_id
            try:
                if not os.path.lexists(spool):
                    if local.get('started_attempt')==record.attempt:raise CloudError('result_capture_unconfirmed')
                    local['started_attempt']=record.attempt
                    await run_blocking(write_json,self.path(plan.result_id),local)
                self.freezing=True
                try:
                    await await_settled(run_blocking(freeze_result,self.root,Path(b.input_directory),spool,plan,
                        record.boundary,record.generation,b.result_limits,b.input_limits))
                finally:self.freezing=False
                state=await run_blocking(verify_spool,spool,plan,record.boundary,b.result_limits)
                received=ResultRecord.model_validate(await task_request(client.http,b,'POST',path+'/prepare',value=state.bundle.model_dump(mode='json')))
                if received.bundle!=state.bundle:raise CloudError('result_identity_conflict')
                for file in state.bundle.files:
                    await upload_record(client,file.artifact,spool/'files'/file.artifact.artifact_id)
                received=ResultRecord.model_validate(await task_request(client.http,b,'POST',path+'/commit',value={}))
                if received.phase!='available' or received.bundle!=state.bundle:raise CloudError('result_identity_conflict')
                await run_blocking(self.retire,local)
            except CloudError as error:
                logger.warning('runner_result_capture_failed code=%s',error.code)
                # Frozen bytes remain available for an explicit retry. A lost commit
                # ACK cannot demote an already available Coordinator result.
                await task_request(client.http,b,'POST',path+'/fail',value={'error_code':error.code})
