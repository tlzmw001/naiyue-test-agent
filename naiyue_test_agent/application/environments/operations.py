"""Durable controller facts: mutations submit once; recovery only queries."""
from contextlib import contextmanager
from contextvars import Context
import asyncio
import base64
import logging
from threading import Condition,RLock
import time
from langchain_core.tools import ToolException
from naiyue_test_agent.contracts.sandbox import SandboxError,SandboxRecord,PROCESS_CLEANUP_ERROR_CODE
from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.contracts.git_inputs import GitInputError,input_payload
from naiyue_test_agent.execution.sandbox_output import SandboxOutput
from naiyue_test_agent.storage.sandbox_operations import SandboxOperations
from naiyue_test_agent.tools.processes import ProcessCleanupError

logger=logging.getLogger(__name__)
TERMINAL={'completed','failed','stopped'}


class SandboxOperationService:
    def __init__(self,environments,*,activity):
        self.environments=environments
        self.activity=activity
        self._condition=Condition(RLock());self._locks={};self._submitting=0;self._closing=False
        self._watchers={}
        self._cleaning=set();self._archive_users={}

    def records(self):return SandboxOperations(self.environments.records().database)

    def _lock(self,ident):
        with self._condition:return self._locks.setdefault(ident,RLock())

    @contextmanager
    def output_guard(self,project_id):
        with self._condition:
            if project_id in self._cleaning or self._archive_users.get(project_id,0):
                raise ValueError('Workspace has active sandbox output operations')
            self._cleaning.add(project_id)
        try:
            for row in self.records().list():
                if row['project_id']!=project_id or row['kind'] not in ('shell','argv'):continue
                if self.environments.get(row['binding_id']).lifecycle=='destroyed':continue
                if row['stale'] or not self.finished(row):
                    raise ValueError('Workspace has active sandbox tasks or unconfirmed output')
            yield
        finally:
            with self._condition:self._cleaning.remove(project_id)

    @contextmanager
    def _archive_activity(self,project_id):
        with self._condition:
            if project_id in self._cleaning:raise SandboxError('env_output_busy')
            self._archive_users[project_id]=self._archive_users.get(project_id,0)+1
        try:yield
        finally:
            with self._condition:
                self._archive_users[project_id]-=1
                if not self._archive_users[project_id]:del self._archive_users[project_id]

    @contextmanager
    def accepting(self):
        with self._condition:
            if self._closing:raise SandboxError('env_stopped')
            self._submitting+=1
        try:yield
        finally:
            with self._condition:
                self._submitting-=1;self._condition.notify_all()

    def _rpc(self,binding,operation,params,*,mutates=False):
        with self.activity.use(binding,used=False):
            row=self.environments.get(binding)
            if row.lifecycle!='running':raise SandboxError('env_stopped' if row.lifecycle=='stopped' else 'env_expired')
            return self.environments._engine(row).request(row,operation,params,mutates=mutates)

    def unsettled(self,binding):
        return any(row['stale'] or not self.finished(row) for row in self.records().list(binding))

    def submit(self,binding,ident,kind,payload,metadata):
        with self.accepting(),self.activity.use(binding),self._lock(ident):
            env=self.environments.validate(binding,require_input=False) if kind=='git_input' else self.environments.validate(binding)
            with self._archive_activity(env.project_id):
                records=self.records()
                row,new=records.reserve(binding,env.project_id,env.session_id,ident,kind,payload,metadata)
                if new:
                    try:
                        remote=self._rpc(binding,'operation_submit',dict(operation_id=ident,kind=kind,payload=payload,
                            metadata={key:metadata[key] for key in ('agent_path',) if key in metadata}),mutates=True)
                        self._merge(row,remote)
                    except GitInputError as error:
                        logger.warning('sandbox_input_rejected code=%s',error.code)
                        return records.save(binding,ident,dict(state='failed',stale=False,error_code=error.code,
                            result=dict(ok=False,error={'code':error.code,'message':error.code})))
                    except (SandboxError,ToolException) as error:
                        logger.warning('sandbox_submit_unconfirmed kind=%s error_type=%s',kind,type(error).__name__)
                        records.save(binding,ident,dict(state='unknown',stale=True,error_code=getattr(error,'code','env_operation_unknown')))
                return self.refresh(binding,ident)


    def _merge(self,local,remote):
        if (not isinstance(remote,dict) or remote.get('operation_id')!=local['operation_id']
                or remote.get('kind')!=local['kind'] or remote.get('fingerprint')!=local['fingerprint']
                or remote.get('state') not in {'starting','running','completed','failed','stopped','unknown'}
                or type(remote.get('observed_bytes')) is not int
                or remote['observed_bytes']<max(local['observed_bytes'],local['metadata'].get('offset',0))):
            raise SandboxError('env_protocol_mismatch')
        return self.records().save(local['binding_id'],local['operation_id'],dict(state=remote['state'],
            result=remote.get('result'),observed_bytes=remote['observed_bytes'],stale=False,error_code=None))

    def _archive(self,row):
        root=self.environments.projects.data_dir/'workspaces'/row['project_id']/'artifacts'
        return SandboxOutput(root,row['operation_id'])

    def refresh(self,binding,ident):
        with self.activity.use(binding,used=False):return self._refresh(binding,ident)

    def _refresh(self,binding,ident):
        with self._lock(ident):
            records=self.records();row=records.get(binding,ident)
            previously_finished=self.finished(row)
            with self._archive_activity(row['project_id']):
                try:
                    remote=self._rpc(binding,'operation_status',dict(operation_id=ident))
                    row=self._merge(row,remote)
                    if row['kind'] in ('shell','argv'):
                        archive=self._archive(row)
                        metadata=archive.prepare(row['metadata'])
                        if metadata!=row['metadata']:row=records.save(binding,ident,dict(metadata=metadata))
                        for _ in range(8):
                            if metadata['offset']>=row['observed_bytes']:break
                            frame=self._rpc(binding,'operation_output',dict(operation_id=ident,offset=metadata['offset'],limit=65536))
                            if not isinstance(frame,dict) or set(frame)!={'offset','next_offset','observed_bytes','first_offset','gap','bytes_base64'}:
                                raise SandboxError('env_protocol_mismatch')
                            try:
                                frame=dict(frame);frame['data']=base64.b64decode(frame.pop('bytes_base64'),validate=True)
                            except (ValueError,TypeError) as error:raise SandboxError('env_protocol_mismatch') from error
                            if (len(frame['data'])>65536 or type(frame['gap']) is not bool or
                                    type(frame['first_offset']) is not int or not 0<=frame['first_offset']<2**63
                                    or type(frame['observed_bytes']) is not int or frame['observed_bytes']<row['observed_bytes']):
                                raise SandboxError('env_protocol_mismatch')
                            if frame['next_offset']==metadata['offset'] and row['observed_bytes']>metadata['offset']:
                                raise SandboxError('env_protocol_mismatch')
                            metadata=archive.append(metadata,frame)
                            row=records.save(binding,ident,dict(metadata=metadata,observed_bytes=frame['observed_bytes']))
                    if self.finished(row) and not previously_finished:self.activity.touch(binding)
                    return row
                except (SandboxError,ToolException,ProjectError) as error:
                    logger.warning('sandbox_status_unavailable error_type=%s',type(error).__name__)
                    return records.save(binding,ident,dict(stale=True,error_code=getattr(error,'code','env_operation_unknown'),
                        state=row['state'] if row['state'] in TERMINAL else 'unknown'))
                except OSError as error:
                    logger.error('sandbox_archive_failed error_type=%s',type(error).__name__)
                    records.save(binding,ident,dict(state='unknown',stale=True,error_code='archive_error'))
                    try:self._rpc(binding,'operation_stop',dict(operation_id=ident),mutates=True)
                    except Exception as stop_error:logger.error('sandbox_archive_stop_unconfirmed error_type=%s',type(stop_error).__name__)
                    raise ToolException('Sandbox archive failed; inspect the original operation before retrying.') from error


    def stop(self,binding,ident):
        with self.activity.use(binding),self._lock(ident):
            row=self.records().get(binding,ident)
            try:
                remote=self._rpc(binding,'operation_stop',dict(operation_id=ident),mutates=True)
                row=self._merge(row,remote)
            except (SandboxError,ToolException,ProjectError) as error:
                logger.error('sandbox_stop_unconfirmed error_type=%s',type(error).__name__)
                row=self.records().save(binding,ident,dict(state='unknown',stale=True,error_code='env_stop_unconfirmed'))
                try:
                    remote=self._rpc(binding,'operation_status',dict(operation_id=ident))
                    row=self._merge(row,remote)
                except (SandboxError,ToolException,ProjectError) as query_error:
                    logger.warning('sandbox_stop_status_unavailable error_type=%s',type(query_error).__name__)
                self.raise_process_failure(row)
                self.records().save(binding,ident,dict(state='unknown',stale=True,error_code='env_stop_unconfirmed'))
                raise SandboxError('env_stop_unconfirmed') from error
            if row['state'] not in TERMINAL:raise SandboxError('env_stop_unconfirmed')
            row=self.refresh(binding,ident)
            while row['state'] in TERMINAL and not self.finished(row) and not row['stale']:
                row=self.refresh(binding,ident)
            if row['state'] not in TERMINAL:
                self.records().save(binding,ident,dict(state='unknown',stale=True,error_code='env_stop_unconfirmed'))
                raise SandboxError('env_stop_unconfirmed')
            return row

    @staticmethod
    def finished(row):
        return row['state'] in TERMINAL and row['metadata'].get('offset',0)>=row['observed_bytes']

    @staticmethod
    def raise_process_failure(row):
        result=row.get('result')
        if row.get('kind') not in ('shell','argv') or row.get('state')!='unknown' or not isinstance(result,dict):return
        error=result.get('error')
        if result.get('ok') is False and isinstance(error,dict) and error.get('code')==PROCESS_CLEANUP_ERROR_CODE:
            raise ProcessCleanupError(f'Sandbox process cleanup could not be confirmed; operation_id={row["operation_id"]}.')

    @staticmethod
    def value(row):
        SandboxOperationService.raise_process_failure(row)
        result=row.get('result')
        if row['state']=='unknown' or not isinstance(result,dict):raise SandboxError('env_operation_unknown')
        if result.get('ok') is not True:
            error=result.get('error',{})
            if error.get('code')=='tool_error':raise ToolException(error.get('message','Sandbox operation failed'))
            raise SandboxError(error.get('code','env_operation_unknown'))
        value=result.get('value')
        if row['kind'] in ('shell','argv'):
            if (not isinstance(value,dict) or value.get('termination') not in ('exited','timeout','user_stop')
                    or value.get('returncode') is not None and type(value['returncode']) is not int
                    or type(value.get('backpressure_count')) is not int or value['backpressure_count']<0
                    or type(value.get('output_complete')) is not bool
                    or any(value.get(key) is not True for key in ('process_reaped','pipes_closed','consumer_settled'))):
                raise SandboxError('env_operation_unknown')
        elif row['kind']=='git_input':
            input_payload(value)
        elif row['kind']=='git_result':
            from naiyue_test_agent.contracts.git_results import result_receipt
            result_receipt(value)
        elif not isinstance(value,list) or len(value)!=2 or not isinstance(value[0],str) or not isinstance(value[1],dict):
            raise SandboxError('env_operation_unknown')
        return value

    def file_call(self,binding,kind,payload,control=None):
        ident=payload['subject'].get('operation_id')
        row=self.submit(binding,ident,kind,payload,{})
        settings=self.environments.get(binding).settings
        while row['state'] not in TERMINAL:
            if row['stale'] or row['state']=='unknown':raise ToolException(f'env_operation_unknown; operation_id={ident}')
            if self._closing or control is not None and control.requested:
                row=self.stop(binding,ident)
                break
            time.sleep(settings['poll_milliseconds']/1000)
            row=self.refresh(binding,ident)
        return self.value(row)

    def close(self):
        with self._condition:
            self._closing=True
            self._condition.wait_for(lambda:self._submitting==0)
        if not (self.environments.projects.data_dir/'projects.sqlite').exists():return
        errors=[]
        for row in self.records().list():
            try:
                if self.environments.get(row['binding_id']).lifecycle=='destroyed':continue
                if row['state'] in TERMINAL:
                    while not self.finished(row) and not row['stale']:
                        row=self.refresh(row['binding_id'],row['operation_id'])
                    continue
                if row['kind'] in ('write','edit','git_result') and row['state']=='unknown':
                    row=self.refresh(row['binding_id'],row['operation_id'])
                    if row['state']=='unknown' and not row['stale']:continue
                self.stop(row['binding_id'],row['operation_id'])
            except Exception as error:
                errors.append(error);logger.error('sandbox_shutdown_stop_unconfirmed error_type=%s',type(error).__name__)
        if errors:raise errors[0]

    def watch(self,binding,ident,poll_milliseconds):
        key=(binding,ident)
        previous=self._watchers.get(key)
        if previous is not None and not previous.done():return
        async def collect():
            from naiyue_test_agent.agent_runtime.async_utils import run_blocking
            try:
                while not self._closing:
                    try:row=await run_blocking(self.refresh,binding,ident)
                    except SandboxError as error:
                        if error.code!='env_busy':raise
                    else:
                        if self.finished(row) or row['stale'] or row['state']=='unknown':return
                    await asyncio.sleep(poll_milliseconds/1000)
            except Exception as error:logger.error('sandbox_output_collection_failed error_type=%s',type(error).__name__)
        task=asyncio.create_task(collect(),context=Context())
        self._watchers[key]=task

    async def aclose(self):
        from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
        try:await run_blocking(self.close)
        finally:
            await await_settled(asyncio.gather(*self._watchers.values(),return_exceptions=True))
