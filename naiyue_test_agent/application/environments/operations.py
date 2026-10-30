"""Durable controller facts: mutations submit once; recovery only queries."""
from contextlib import contextmanager,asynccontextmanager
from contextvars import Context
import asyncio
import base64
import logging
from naiyue_test_agent.diagnostics import safe_log, diagnostic_text
from threading import Condition,RLock
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.execution.sandbox_channel import SubmittedRequestCancelled,RequestNotSent
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
        self._submissions={}
        self._cleaning=set();self._archive_users={}

    def records(self):return SandboxOperations(self.environments.records().database)

    @property
    def runtime(self):return self.environments.transport.runtime

    @asynccontextmanager
    async def _operation_lock(self,binding,ident):
        # All async operation algorithms run on the transport owner's loop.
        key=binding,ident
        entry=self._locks.setdefault(key,[asyncio.Lock(),0]);entry[1]+=1
        try:
            async with entry[0]:yield
        finally:
            entry[1]-=1
            if not entry[1]:del self._locks[key]

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

    async def _rpc(self,binding,operation,params,*,mutates=False):
        with self.activity.use(binding,used=False):
            row=await run_blocking(self.environments.get,binding)
            if row.lifecycle!='running':raise SandboxError('env_stopped' if row.lifecycle=='stopped' else 'env_expired')
            return await self.environments.transport.arequest(row,operation,params,mutates=mutates)

    def unsettled(self,binding):
        return any(row['stale'] or not self.finished(row) for row in self.records().list(binding))

    def submit(self,binding,ident,kind,payload,metadata):
        return self.runtime.call(self._submit(binding,ident,kind,payload,metadata))

    async def asubmit(self,binding,ident,kind,payload,metadata):
        return await self.runtime.await_call(self._submit(binding,ident,kind,payload,metadata))

    async def _submit(self,binding,ident,kind,payload,metadata):
      async with self.activity.ause(binding):
        with self.accepting():
          async with self._operation_lock(binding,ident):
            submitted=asyncio.Event();self._submissions[binding,ident]=submitted
            try:
                failed=await self._submit_once(binding,ident,kind,payload,metadata)
            finally:
                submitted.set();self._submissions.pop((binding,ident),None)
            if failed is not None:return failed
          return await self._refresh(binding,ident)

    async def _submit_once(self,binding,ident,kind,payload,metadata):
            env=await run_blocking(self.environments.check,binding,require_input=kind!='git_input')
            with self._archive_activity(env.project_id):
                records=await run_blocking(self.records)
                row,new=await run_blocking(records.reserve,binding,env.project_id,env.session_id,ident,kind,payload,metadata)
                if not new and self.finished(row) and not row['stale']:return row
                if new:
                    try:
                        remote=await self._rpc(binding,'operation_submit',dict(operation_id=ident,kind=kind,payload=payload,
                            metadata={key:metadata[key] for key in ('agent_path',) if key in metadata}),mutates=True)
                        row=await run_blocking(self._merge,row,remote)
                        if self.finished(row):return row
                    except SubmittedRequestCancelled as error:
                        if error.error is None:
                            try:await run_blocking(self._merge,row,error.result)
                            except Exception as save_error:
                                logger.error('sandbox_cancelled_receipt_save_failed error_type=%s',type(save_error).__name__)
                                await run_blocking(records.save,binding,ident,dict(state='unknown',stale=True,error_code='env_operation_unknown'))
                        elif isinstance(error.error,(RequestNotSent,GitInputError)):
                            code=error.error.code
                            await run_blocking(records.save,binding,ident,dict(state='failed',stale=False,error_code=code,
                                result=dict(ok=False,error={'code':code,'message':code})))
                        else:
                            logger.warning('sandbox_cancelled_submit_unconfirmed error_type=%s',type(error.error).__name__)
                            await run_blocking(records.save,binding,ident,dict(state='unknown',stale=True,error_code='env_operation_unknown'))
                        raise
                    except (RequestNotSent,GitInputError) as error:
                        logger.warning('sandbox_input_rejected code=%s',error.code)
                        return await run_blocking(records.save,binding,ident,dict(state='failed',stale=False,error_code=error.code,
                            result=dict(ok=False,error={'code':error.code,'message':error.code})))
                    except (SandboxError,ToolException) as error:
                        logger.warning('sandbox_submit_unconfirmed kind=%s error_type=%s',kind,type(error).__name__)
                        await run_blocking(records.save,binding,ident,dict(state='unknown',stale=True,error_code=getattr(error,'code','env_operation_unknown')))


    def _merge(self,local,remote):
        if (not isinstance(remote,dict) or remote.get('operation_id')!=local['operation_id']
                or remote.get('kind')!=local['kind'] or remote.get('fingerprint')!=local['fingerprint']
                or remote.get('state') not in {'starting','running','completed','failed','stopped','unknown'}
                or type(remote.get('observed_bytes')) is not int
                or remote['observed_bytes']<max(local['observed_bytes'],local['metadata'].get('offset',0))):
            raise SandboxError('env_protocol_mismatch')
        changes=dict(state=remote['state'],result=remote.get('result'),
            observed_bytes=remote['observed_bytes'],stale=False,error_code=None)
        if all(local.get(key)==value for key,value in changes.items()):return local
        return self.records().save(local['binding_id'],local['operation_id'],changes)

    def _archive(self,row):
        root=self.environments.projects.data_dir/'workspaces'/row['project_id']/'artifacts'
        return SandboxOutput(root,row['operation_id'])

    def _prepare_archive(self,row,archive):
        # Creation and its identity save are one cancellation custody boundary.
        metadata=archive.prepare(row['metadata'])
        if metadata!=row['metadata']:
            row=self.records().save(row['binding_id'],row['operation_id'],dict(metadata=metadata))
        return row

    def refresh(self,binding,ident):
        return self.runtime.call(self._refresh(binding,ident))

    async def arefresh(self,binding,ident):
        return await self.runtime.await_call(self._refresh(binding,ident))

    async def background(self,binding,ident,limit):
        return await self.runtime.await_call(self._background(binding,ident,limit))

    async def _background(self,binding,ident,limit):
        async with self._operation_lock(binding,ident):
            records=await run_blocking(self.records)
            row=await run_blocking(records.get,binding,ident)
            remote=await self._rpc(binding,'operation_background',dict(operation_id=ident,output_limit=limit),mutates=True)
            row=await run_blocking(self._merge,row,remote)
            self.raise_process_failure(row)
            metadata={**row['metadata'],'background':True,'max_archive_bytes':limit}
            return await run_blocking(records.save,binding,ident,dict(metadata=metadata))

    async def _refresh(self,binding,ident):
        with self.activity.use(binding,used=False):
          async with self._operation_lock(binding,ident):
            records=await run_blocking(self.records);row=await run_blocking(records.get,binding,ident)
            previously_finished=self.finished(row)
            with self._archive_activity(row['project_id']):
                phase='status'
                facts=dict(binding_id=binding,operation_id=ident,project_id=row['project_id'])
                try:
                    poll=await self._rpc(binding,'operation_poll',dict(operation_id=ident,
                        offset=row['metadata'].get('offset',0),limit=524288))
                    if not isinstance(poll,dict) or set(poll)!={'operation','output'}:raise SandboxError('env_protocol_mismatch')
                    row=await run_blocking(self._merge,row,poll['operation'])
                    if row['kind'] in ('shell','argv'):
                        archive=self._archive(row)
                        phase='archive_prepare_and_save'
                        row=await run_blocking(self._prepare_archive,row,archive)
                        metadata=row['metadata']
                        if metadata['offset']<row['observed_bytes']:
                            phase='output_fetch'
                            frame=poll['output']
                            if not isinstance(frame,dict) or set(frame)!={'offset','next_offset','observed_bytes','first_offset','gap','bytes_base64'}:
                                raise SandboxError('env_protocol_mismatch')
                            try:
                                frame=dict(frame);frame['data']=base64.b64decode(frame.pop('bytes_base64'),validate=True)
                            except (ValueError,TypeError) as error:raise SandboxError('env_protocol_mismatch') from error
                            if (len(frame['data'])>524288 or type(frame['gap']) is not bool or
                                    type(frame['first_offset']) is not int or not 0<=frame['first_offset']<2**63
                                    or type(frame['observed_bytes']) is not int or frame['observed_bytes']<row['observed_bytes']):
                                raise SandboxError('env_protocol_mismatch')
                            if frame['next_offset']==metadata['offset'] and row['observed_bytes']>metadata['offset']:
                                raise SandboxError('env_protocol_mismatch')
                            phase='archive_append'
                            metadata=await run_blocking(archive.append,metadata,frame)
                            phase='metadata_save'
                            row=await run_blocking(records.save,binding,ident,dict(metadata=metadata,observed_bytes=frame['observed_bytes']))
                    if self.finished(row) and not previously_finished:await run_blocking(self.activity.touch,binding)
                    return row
                except (SandboxError,ToolException,ProjectError) as error:
                    safe_log(logger.warning, 'sandbox_status_unavailable error_type=%s diagnostic=%s',type(error).__name__,
                        diagnostic_text(error=error,phase=phase,code=getattr(error,'code',None),**facts))
                    return await run_blocking(records.save,binding,ident,dict(stale=True,error_code=getattr(error,'code','env_operation_unknown')))
                except OSError as error:
                    safe_log(logger.error, 'sandbox_archive_failed error_type=%s diagnostic=%s',type(error).__name__,
                        diagnostic_text(error=error,phase=phase,offset=row['metadata'].get('offset'),
                            saved_bytes=row['metadata'].get('saved_bytes'),observed_bytes=row['observed_bytes'],**facts))
                    await run_blocking(records.save,binding,ident,dict(state='unknown',stale=True,error_code='archive_error'))
                    try:await self._rpc(binding,'operation_stop',dict(operation_id=ident),mutates=True)
                    except Exception as stop_error:safe_log(logger.error, 'sandbox_archive_stop_unconfirmed error_type=%s diagnostic=%s',
                        type(stop_error).__name__,diagnostic_text(error=stop_error,phase='stop',**facts))
                    raise ToolException('Sandbox archive failed; inspect the original operation before retrying.') from error


    def stop(self,binding,ident):
        return self.runtime.call(self._stop(binding,ident))

    async def astop(self,binding,ident):
        return await self.runtime.await_call(self._stop(binding,ident))

    async def _stop(self,binding,ident):
        async with self.activity.ause(binding):
            submitted=self._submissions.get((binding,ident))
            if submitted is not None:await submitted.wait()
            records=await run_blocking(self.records)
            row=await run_blocking(records.get,binding,ident)
            try:
                # The reserved control slot bypasses output/archive collection.
                remote=await self._rpc(binding,'operation_stop',dict(operation_id=ident),mutates=True)
                if not isinstance(remote,dict) or remote.get('state') not in TERMINAL:raise SandboxError('env_stop_unconfirmed')
                async with self._operation_lock(binding,ident):
                    row=await run_blocking(records.get,binding,ident)
                    row=await run_blocking(self._merge,row,remote)
            except SubmittedRequestCancelled as error:
                async with self._operation_lock(binding,ident):
                    row=await run_blocking(records.get,binding,ident)
                    if error.error is None:
                        await run_blocking(self._merge,row,error.result)
                    else:
                        logger.warning('sandbox_cancelled_stop_unconfirmed error_type=%s',type(error.error).__name__)
                        await run_blocking(records.save,binding,ident,dict(error_code='env_stop_unconfirmed',
                            stale=row['stale'] if isinstance(error.error,RequestNotSent) else True))
                raise
            except (SandboxError,ToolException,ProjectError) as error:
                safe_log(logger.error, 'sandbox_stop_unconfirmed error_type=%s diagnostic=%s',type(error).__name__,
                    diagnostic_text(error=error,binding_id=binding,operation_id=ident,phase='stop',code=getattr(error,'code',None)))
                async with self._operation_lock(binding,ident):
                    row=await run_blocking(records.get,binding,ident)
                    try:
                        remote=await self._rpc(binding,'operation_status',dict(operation_id=ident))
                        row=await run_blocking(self._merge,row,remote)
                    except (SandboxError,ToolException,ProjectError) as query_error:
                        safe_log(logger.warning, 'sandbox_stop_status_unavailable error_type=%s diagnostic=%s',type(query_error).__name__,
                            diagnostic_text(error=query_error,binding_id=binding,operation_id=ident,phase='stop_status'))
                        row=await run_blocking(records.save,binding,ident,dict(stale=True,error_code='env_stop_unconfirmed'))
                    self.raise_process_failure(row)
                    if row['state'] not in TERMINAL or row['stale']:
                        await run_blocking(records.save,binding,ident,dict(error_code='env_stop_unconfirmed'))
                        raise SandboxError('env_stop_unconfirmed') from error
            row=await self._refresh(binding,ident)
            while row['state'] in TERMINAL and not self.finished(row) and not row['stale']:
                row=await self._refresh(binding,ident)
            if row['state'] not in TERMINAL:
                await run_blocking(records.save,binding,ident,dict(state='unknown',stale=True,error_code='env_stop_unconfirmed'))
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

    async def afile_call(self,binding,kind,payload,control=None):
        return await self.runtime.await_call(self._file_call(binding,kind,payload,control))

    async def _file_call(self,binding,kind,payload,control=None):
        ident=payload['subject'].get('operation_id')
        row=await self._submit(binding,ident,kind,payload,{})
        settings=(await run_blocking(self.environments.get,binding)).settings
        while row['state'] not in TERMINAL:
            if row['stale'] or row['state']=='unknown':raise ToolException(f'env_operation_unknown; operation_id={ident}')
            if self._closing or control is not None and control.requested:
                row=await self._stop(binding,ident)
                break
            await asyncio.sleep(settings['poll_milliseconds']/1000)
            row=await self._refresh(binding,ident)
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
            failures=0
            try:
                while not self._closing:
                    try:row=await self.arefresh(binding,ident)
                    except SandboxError as error:
                        if error.code!='env_busy':raise
                    else:
                        if not row['stale'] and (self.finished(row) or row['state']=='unknown'):return
                        failures=failures+1 if row['stale'] else 0
                        if not row['stale'] and row['metadata'].get('offset',0)<row['observed_bytes']:continue
                    await asyncio.sleep(min(5,poll_milliseconds/1000*2**min(failures,6)))
            except Exception as error:safe_log(logger.error, 'sandbox_output_collection_failed error_type=%s diagnostic=%s',type(error).__name__,
                diagnostic_text(error=error,binding_id=binding,operation_id=ident,phase='refresh',collector_stopped=True))
        task=asyncio.create_task(collect(),context=Context())
        self._watchers[key]=task
        def retired(finished):
            if self._watchers.get(key) is finished:self._watchers.pop(key)
        task.add_done_callback(retired)

    async def aclose(self):
        from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
        try:await run_blocking(self.close)
        finally:
            await await_settled(asyncio.gather(*self._watchers.values(),return_exceptions=True))
