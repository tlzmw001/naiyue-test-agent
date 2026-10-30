"""Envd retains process custody after the requesting client disconnects."""
import base64
import logging
from uuid import uuid4
from naiyue_test_agent.diagnostics import safe_log, diagnostic_text
from pathlib import Path
from threading import Event,RLock,Thread
from naiyue_test_agent.contracts.sandbox import SandboxError,PROTOCOL_VERSION,PROCESS_CLEANUP_ERROR_CODE
from naiyue_test_agent.tools.processes import ProcessCleanupError
from naiyue_test_agent.storage.sandbox_journal import SandboxJournal
from .dispatch import FileDispatcher
from .processes import execute_process
from .protocol import check_request
from .git_inputs import InputReceiver
from .git_results import ResultExporter
from naiyue_test_agent.contracts.git_inputs import GitInputError

logger=logging.getLogger(__name__)


class OperationControl:
    def __init__(self):self.event=Event();self.output_limit=None;self.stop_reason=None
    @property
    def requested(self):return self.event.is_set()


class EnvironmentOperations:
    def __init__(self,environment,session_id,state_root,settings):
        self.environment,self.session_id,self.settings=environment,session_id,settings
        self.generation=uuid4().hex
        self.journal=SandboxJournal(Path(state_root)/'journal.sqlite',
            dict(environment=environment.public(),session_id=session_id),settings)
        self.files=FileDispatcher(environment,session_id,max_message_bytes=settings.max_message_bytes)
        self.inputs=InputReceiver(state_root,environment,settings)
        self.results=ResultExporter(state_root,environment,session_id,settings,self.inputs)
        self._lock=RLock();self._active={};self._closing=False;self._readers=0
        self._input_pending=False;self._result_pending=False
        self._searches={}
        installation=self.inputs.installation()
        if installation is not None:
            try:self._input_pending=self.journal.get(installation['operation_id'])['state']!='completed'
            except SandboxError as error:
                if error.code!='env_operation_not_found':raise
                self._input_pending=True

    def submit(self,ident,kind,payload,metadata):
        if kind not in ('write','edit','shell','argv','git_input','git_result'):raise SandboxError('env_operation_unsupported')
        with self._lock:
            if self._closing:raise SandboxError('env_stopped')
            try:existing=self.journal.get(ident)
            except SandboxError as error:
                if error.code!='env_operation_not_found':raise
                existing=None
            if existing is not None:return self.journal.reserve(ident,kind,payload,metadata)[0]
            if self._input_pending:raise GitInputError('git_input_install_unconfirmed')
            if self._result_pending:raise GitInputError('git_result_busy')
            if kind=='git_result':
                if payload.get('result_id')!=ident:raise GitInputError('git_result_binding_mismatch')
                if self._active or self._readers or self.journal.unconfirmed_process():raise GitInputError('git_result_busy')
            if kind=='git_input' and (self._active or self._readers):raise GitInputError('git_input_busy')
            row,new=self.journal.reserve(ident,kind,payload,metadata)
            if not new:return row
            if kind=='git_result':self._result_pending=True
            if kind=='git_input':
                try:self.inputs.reserve_install(ident,payload)
                except GitInputError as error:
                    self.journal.finish(ident,'failed',dict(ok=False,error={'code':error.code,'message':error.code}))
                    return self.journal.get(ident)
                except BaseException:
                    self._input_pending=True;self.journal.finish(ident,'unknown');raise
                self._input_pending=True
            control=OperationControl();done=Event()
            control.output_limit=payload.get('output_limit')
            worker=Thread(target=self._execute,args=(ident,kind,payload,control,done),daemon=True)
            self._active[ident]=(control,done,worker)
            try:worker.start()
            except BaseException:
                self._active.pop(ident);self._result_pending=False;self.journal.finish(ident,'unknown');raise
        return self.journal.get(ident)

    def _execute(self,ident,kind,payload,control,done):
        from langchain_core.tools import ToolException
        from naiyue_test_agent.tools.write import WriteOutcomeUnknown
        phase='execute'
        facts=dict(binding_id=self.environment.binding_id,session_id=self.session_id,operation_id=ident,kind=kind)
        try:
            if kind=='git_input':
                result=self.inputs.install(payload,control)
                state='completed'
            elif kind=='git_result':
                result=self.results.capture(payload,control)
                state='completed'
            elif kind in ('write','edit'):
                request=dict(protocol=PROTOCOL_VERSION,request_id=ident,environment=self.environment.public(),
                    session_id=self.session_id,operation=kind,params=payload)
                result=self.files.dispatch(request,control=control)
                state='completed'
            else:
                async def consume(_stream,data):
                    self.journal.append(ident,data)
                    if control.output_limit and self.journal.get(ident)['observed_bytes']>=control.output_limit:
                        control.stop_reason='output_limit';control.event.set()
                result=execute_process(payload,kind,Path(self.environment.root),consume,control,
                    lambda:self.journal.finish(ident,'running'))
                state='stopped' if result['termination']=='user_stop' else 'completed'
                result['stop_reason']=control.stop_reason
            phase='terminal_save'
            self.journal.finish(ident,state,dict(ok=True,value=result))
            if kind=='git_input':
                with self._lock:self._input_pending=False
        except Exception as error:
            safe_log(logger.error, 'sandbox_operation_failed kind=%s error_type=%s diagnostic=%s',kind,type(error).__name__,
                diagnostic_text(error=error,phase=phase,**facts))
            known=(isinstance(error,ToolException) and not isinstance(error,WriteOutcomeUnknown)
                or kind in ('git_input','git_result') and isinstance(error,GitInputError))
            try:
                self.journal.finish(ident,'failed' if known else 'unknown',dict(ok=False,error={
                    'code':error.code if kind in ('git_input','git_result') and isinstance(error,GitInputError) else
                        PROCESS_CLEANUP_ERROR_CODE if isinstance(error,ProcessCleanupError) else
                        'tool_error' if known else 'env_operation_unknown',
                    'message':'Process cleanup could not be confirmed.' if isinstance(error,ProcessCleanupError) else
                        str(error)[:4096] if known else type(error).__name__}))
            except Exception as secondary:
                safe_log(logger.error, 'sandbox_operation_failure_save_unconfirmed diagnostic=%s',
                    diagnostic_text(error=secondary,phase='failure_save',cause_type=type(error).__name__,**facts))
                raise
        finally:
            with self._lock:
                self._active.pop(ident,None)
                if kind=='git_result':self._result_pending=False
            done.set()

    def wait(self,ident,timeout):
        with self._lock:active=self._active.get(ident)
        return active[1].wait(timeout) if active else True

    def stop(self,ident):
        with self._lock:
            row=self.journal.get(ident);active=self._active.get(ident)
            if active:active[0].event.set()
        if active and not active[1].wait(self.settings.stop_timeout_seconds):raise SandboxError('env_stop_unconfirmed')
        row=self.journal.get(ident)
        if row['state'] in ('unknown','starting','running'):raise SandboxError('env_stop_unconfirmed')
        return row

    def dispatch(self,request):
        if 'generation' in request:
            request=dict(request)
            if request.pop('generation')!=self.generation:raise SandboxError('env_binding_mismatch')
        check_request(request,self.environment,self.session_id)
        op,p=request['operation'],request['params']
        if op=='transport_hello':
            return dict(attach=1,protocol=PROTOCOL_VERSION,generation=self.generation,
                environment=self.environment.public(),session_id=self.session_id)
        if op in ('request_register','request_cancel','request_release'):
            ident=p.get('request_id')
            if not isinstance(ident,str) or not 1<=len(ident)<=64:raise SandboxError('env_protocol_mismatch')
            with self._lock:
                search=self._searches.get(ident)
                if op=='request_register':
                    if self._closing:raise SandboxError('env_stopped')
                    if search is not None:raise SandboxError('env_operation_conflict')
                    if len(self._searches)>=self.settings.transport.pending_requests:raise SandboxError('env_connection_capacity')
                    self._searches[ident]=[OperationControl(),False,Event()]
                    return {'registered':True}
                if search is not None:
                    search[0].event.set()
                    if op=='request_release' and not search[1]:self._searches.pop(ident)
                return {'cancelled':search is not None}
        if op=='input_probe':return self.inputs.probe(p['original_head'])
        if op=='input_status':return self.inputs.status(p['transfer_id'])
        if op in ('input_begin','input_chunk','input_cache'):
            with self._lock:
                if self._closing:raise SandboxError('env_stopped')
                if self._input_pending:raise GitInputError('git_input_install_unconfirmed')
            if op=='input_begin':return self.inputs.begin(p)
            if op=='input_chunk':return self.inputs.chunk(p['transfer_id'],p['offset'],p['data'])
            return self.inputs.cache(p['transfer_id'])
        if op=='operation_submit':return self.submit(p['operation_id'],p['kind'],p['payload'],p.get('metadata',{}))
        if op=='operation_status':return self.journal.get(p['operation_id'])
        if op=='operation_poll':
            row=self.journal.get(p['operation_id'])
            output=None
            if row['kind'] in ('shell','argv'):
                if type(p['limit']) is not int or not 1<=p['limit']<=524288:
                    raise SandboxError('env_protocol_mismatch')
                output=self.journal.output(p['operation_id'],p['offset'],min(p['limit'],self.settings.max_message_bytes//8))
                output['bytes_base64']=base64.b64encode(output.pop('data')).decode()
            return {'operation':row,'output':output}
        if op=='result_chunk':
            row=self.journal.get(p['result_id'])
            if row['kind']!='git_result' or row['state']!='completed':raise GitInputError('git_result_unconfirmed')
            return self.results.chunk(p['result_id'],p['offset'],p['limit'])
        if op=='operation_stop':return self.stop(p['operation_id'])
        if op=='operation_background':
            with self._lock:
                row=self.journal.get(p['operation_id'])
                if row['metadata'].get('agent_path')!='root' or type(p['output_limit']) is not int or p['output_limit']<=0:
                    raise SandboxError('env_binding_mismatch')
                active=self._active.get(p['operation_id'])
                if active:
                    active[0].output_limit=p['output_limit']
                    if row['observed_bytes']>=p['output_limit']:active[0].stop_reason='output_limit';active[0].event.set()
            return row
        if op=='operation_output':
            value=self.journal.output(p['operation_id'],p['offset'],min(p['limit'],self.settings.max_message_bytes//8))
            value['bytes_base64']=base64.b64encode(value.pop('data')).decode()
            return value
        if op in ('write','edit'):raise SandboxError('env_operation_unsupported')
        search=None
        with self._lock:
            if self._closing:raise SandboxError('env_stopped')
            if self._input_pending and op!='hello':raise GitInputError('git_input_install_unconfirmed')
            if self._result_pending and op!='hello':raise GitInputError('git_result_busy')
            if op in ('find','grep'):
                search=self._searches.get(request['request_id'])
                if search is None:
                    if p.get('registered'):
                        from langchain_core.tools import ToolException
                        raise ToolException('Search was cancelled before execution')
                    if len(self._searches)>=self.settings.transport.pending_requests:raise SandboxError('env_connection_capacity')
                    search=[OperationControl(),False,Event()];self._searches[request['request_id']]=search
                if search[1]:raise SandboxError('env_operation_conflict')
                search[1]=True
            self._readers+=1
        try:
            if op=='result_options':return self.results.options()
            return self.files.dispatch(request,control=search[0] if search else None)
        finally:
            with self._lock:
                self._readers-=1
                if search is not None:
                    self._searches.pop(request['request_id'],None);search[2].set()

    def close(self):
        with self._lock:
            self._closing=True;active=tuple(self._active)
            searches=tuple(self._searches.values())
            for search in searches:search[0].event.set()
            self._searches={key:value for key,value in self._searches.items() if value[1]}
        errors=[]
        for search in searches:
            if search[1] and not search[2].wait(self.settings.stop_timeout_seconds):
                errors.append(SandboxError('env_stop_unconfirmed'))
        for ident in active:
            try:self.stop(ident)
            except Exception as error:
                errors.append(error);logger.error('envd_stop_unconfirmed error_type=%s',type(error).__name__)
        if errors:raise errors[0]
        self.journal.close()
        self.files.files.workspace.close()

    def __enter__(self):return self
    def __exit__(self,*_args):self.close()
