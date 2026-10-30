"""Map the finite file protocol to existing algorithms in the container."""
import asyncio
from types import SimpleNamespace
from naiyue_test_agent.contracts.run_context import RuntimeContext, RunScope, RunIdentity, RunCapabilities, ToolAuthorization
from naiyue_test_agent.contracts.sandbox import SandboxError,PROTOCOL_VERSION
from naiyue_test_agent.tools import settings as budgets
from naiyue_test_agent.tools.permission_rules import PermissionSettings
from naiyue_test_agent.tools.edit import EditReplacement
from .files import EnvdFiles
from .protocol import check_request,pack


class FileDispatcher:
    def __init__(self,environment,session_id,*,max_message_bytes=16777216):
        self.environment,self.session_id=environment,session_id
        self.max_message_bytes=max_message_bytes
        self.files=EnvdFiles(environment,session_id)

    def runtime(self,params,control=None):
        auth=params['access']
        permit=auth['permit']
        if (permit.get('session_id')!=self.session_id or
                permit.get('subject',{}).get('execution_environment')!=self.environment.public()):
            raise SandboxError('env_binding_mismatch')
        scope=RunScope(RunIdentity(self.session_id,agent_path=permit['agent_path'],project_id=self.environment.project_id),
            capabilities=RunCapabilities(files=self.files),environment=self.environment,control=control)
        authorization=ToolAuthorization(permit,PermissionSettings.model_validate(auth['permission_rules']),
            tuple(auth['session_allow']),auth['permission_mode'])
        return SimpleNamespace(context=RuntimeContext(scope,authorization=authorization),tool_call_id=permit['call_id'])

    def dispatch(self,request,control=None):
        check_request(request,self.environment,self.session_id)
        op,p=request['operation'],request['params']
        files=self.files
        if op=='hello':
            files.validate()
            from naiyue_test_agent import __version__
            return {'protocol':PROTOCOL_VERSION,'package_version':__version__,
                    'environment':self.environment.public(),'session_id':self.session_id}
        if op=='prepare_target':return files.prepare_target(p['path'],p['operation'])
        if op=='resource_info':return pack(files.resource_info(p['path']))
        if op=='resolve_resource':return files.resolve_resource(p['path'])
        if op=='list_resources':return files.list_resources(p['path'])
        if op in ('resource_index','resource_read_batch'):
            from .resources import resource_index,resource_read_batch
            return (resource_index if op=='resource_index' else resource_read_batch)(files,p,response_limit=self.max_message_bytes)
        if op=='read_resource':
            if type(p['max_bytes']) is not int or not 0<=p['max_bytes']<=16777216:
                raise SandboxError('env_message_too_large')
            return pack(files.read_resource(p['path'],p['max_bytes']))
        types={'read':budgets.ReadSettings,'write':budgets.WriteSettings,'edit':budgets.EditSettings,
               'list':budgets.LsSettings,'find':budgets.FindSettings,'grep':budgets.GrepSettings}
        if op not in types:raise SandboxError('env_operation_unsupported')
        options=dict(settings=types[op].model_validate(p['settings']),control=control,progress=None)
        if op=='read':return files.read(p['subject'],p['offset'],p['limit'],**options)
        if op=='write':return files.write(p['subject'],p['content'],read_allowed=p['read_allowed'],**options)
        if op=='edit':
            edits=[EditReplacement.model_validate(item) for item in p['edits']]
            return files.edit(p['subject'],edits,**options)
        runtime=self.runtime(p,control)
        options.pop('control')
        if op=='list':return files.list(p['subject'],p['limit'],runtime,**options)
        options.update(process_settings=budgets.ProcessSettings.model_validate(p['process_settings']) if p['process_settings'] else None,
                       timeout=p['timeout'])
        if op=='find':
            return asyncio.run(files.find(p['subject'],p['pattern'],p['limit'],runtime,**options))
        return asyncio.run(files.grep(p['subject'],p['pattern'],p['glob'],p['ignore_case'],p['literal'],p['context_lines'],p['limit'],runtime,**options))
