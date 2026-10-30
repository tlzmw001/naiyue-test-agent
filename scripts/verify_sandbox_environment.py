"""Real Docker gate; run explicitly, never substitute a fake or skip a failure."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from uuid import uuid4


async def scenario(directory,context,image):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage,ToolMessage
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.application import prepare_tool_registry
    from naiyue_test_agent.agent_runtime import build_runtime_agent
    from naiyue_test_agent.agent_runtime.async_utils import run_blocking
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.contracts.run_context import RuntimeContext,RunScope,RunIdentity,RunCapabilities
    from naiyue_test_agent.contracts.sandbox import SandboxError
    from naiyue_test_agent.tools import create_builtin_tools
    from naiyue_test_agent.tools.permission_rules import PermissionSettings
    from naiyue_test_agent.execution.docker_cli import DockerCLI
    class OfflineModel(FakeMessagesListChatModel):
        def bind_tools(self,tools,**kwargs):return self
    settings=AppSettings.model_validate({'storage':{'data_dir':str(directory/'data')},
        'memory':{'auto_enabled':False},'sandbox':{'context':context,'image':image,'network':{'mode':'none'}}})
    specs={spec.tool.name:spec for spec in create_builtin_tools(settings.tools) if spec.tool.name in ('read','write','edit','ls','find','grep')}
    async def invoke(port,name,args,*,rules=None):
        model=OfflineModel(responses=[AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':'C'}]),AIMessage(content='done')])
        registry=prepare_tool_registry([specs[name]])
        agent=build_runtime_agent(model,tool_registry=registry,resolve_active_tool_names=lambda *_a,**_k:registry.names(),
            system_prompt='',response_format=None,resolve_model=lambda *_a,**_k:('fake',model),
            permission_settings=rules,resolve_permission_context=lambda *_a,**_k:{'mode':'BypassApprovals','session_allow':[]})
        scope=RunScope(RunIdentity(port.owner_session_id,run_id=uuid4().hex,project_id=port.environment.project_id),
            capabilities=RunCapabilities(files=port),environment=port.environment)
        result=await agent.ainvoke({'messages':[('user','file operation')]},context=RuntimeContext(scope))
        message=next(value for value in result['messages'] if isinstance(value,ToolMessage))
        return message
    async def expect(port,name,args):
        message=await invoke(port,name,args)
        assert message.status=='success',(name,message.content)
        return message
    def application():
        return open_service_application('unused',tools=tuple(specs.values()),system_prompt='',response_format=None,settings=settings)
    bindings=[]
    project=None
    report={}
    with patch('naiyue_test_agent.application.load_model_catalog',lambda _:ModelCatalog('fake',{'fake':OfflineModel(responses=[AIMessage(content='unused')])})):
        try:
            async with application() as app:
                root=directory/'source';root.mkdir()
                project=await app.projects.add_local(root,trusted=True,operation_id='project')
                host=await app.projects.open(project.project_id)
                sessions=[await host.sessions.create('fake') for i in range(2)]
                for sid in sessions:
                    row=await app.environments.create(project.project_id,sid)
                    bindings.append(row.binding_id)
                ports=[await app.environments.files(binding,project_id=project.project_id,session_id=sid)
                    for binding,sid in zip(bindings,sessions)]
                for port,text in zip(ports,('alpha\n','beta\n')):
                    await expect(port,'write',{'path':'same.txt','content':text})
                assert 'alpha' in (await expect(ports[0],'read',{'path':'same.txt'})).content
                assert 'beta' in (await expect(ports[1],'read',{'path':'same.txt'})).content
                assert not (root/'same.txt').exists()
                await expect(ports[0],'edit',{'path':'same.txt','edits':[{'oldText':'alpha','newText':'updated'}]})
                assert 'updated' in (await expect(ports[0],'read',{'path':'same.txt'})).content
                await expect(ports[0],'write',{'path':'denied.txt','content':'unique-denied-value\n'})
                await expect(ports[0],'write',{'path':'.agents/skills/check/SKILL.md','content':'---\nname: check\ndescription: Check\n---\nDo check\n'})
                await expect(ports[0],'write',{'path':'AGENTS.md','content':'Keep project rules\n'})
                for name,args in [('ls',{'path':'.'}),('find',{'pattern':'*.txt'}),('grep',{'pattern':'updated'})]:
                    result=await expect(ports[0],name,args)
                    assert 'same.txt' in result.content,(name,result.content)
                rules=PermissionSettings.model_validate({'deny':[{'tool':'grep','path':'${workspace}/denied.txt'}]})
                result=await invoke(ports[0],'grep',{'pattern':'unique-denied-value'},rules=rules)
                assert result.status=='success' and 'unique-denied-value' not in result.content,result.content
                agents=await ports[0].read_resource('/workspace/AGENTS.md',4096)
                assert agents==b'Keep project rules\n'
                cli=DockerCLI(settings.sandbox,directory/'data')
                row=await app.environments.get(bindings[0])
                await run_blocking(cli.run,['container','exec',row.container_id,'python','-I','-c',
                    'from PIL import Image;Image.new("RGB",(1,1),(1,2,3)).save("/workspace/pixel.png")'])
                image_result=await expect(ports[0],'read',{'path':'pixel.png'})
                import base64
                block=next(item for item in image_result.content if item.get('type')=='image')
                assert block['mime_type']=='image/png' and base64.b64decode(block['base64']).startswith(b'\x89PNG')
                report['image_read']='passed'
                from naiyue_test_agent.application.sessions.run_resources import prepare_resources
                resources=await prepare_resources(ports[0])
                assert 'Keep project rules' in resources['project_instructions']
                assert any(part.get('count')==1 for part in resources['prompt_parts'])
                for value in ('/etc/passwd','/workspace/../etc/passwd'):
                    result=await invoke(ports[0],'read',{'path':value})
                    assert result.status=='error'
                cli=DockerCLI(settings.sandbox,directory/'data')
                row=await app.environments.get(bindings[0])
                network=await run_blocking(cli.run,['container','exec',row.container_id,'python','-I','-c',
                    'import os,socket;assert os.listdir("/sys/class/net")==["lo"];s=socket.socket();s.bind(("127.0.0.1",0));print("loopback-only")'])
                assert b'loopback-only' in network
                await app.environments.stop(bindings[0])
                result=await invoke(ports[0],'read',{'path':'same.txt'})
                assert result.status=='error' and 'env_stopped' in result.content
                await app.environments.start(bindings[0])
                assert 'updated' in (await expect(ports[0],'read',{'path':'same.txt'})).content
                report.update(file_tools='passed',isolated_volumes='passed',network_none='passed',resources='passed',permission_filter='passed',stop_start='passed')
            async with application() as app:
                await app.environments.start(bindings[0])
                port=await app.environments.files(bindings[0],project_id=project.project_id,session_id=sessions[0])
                assert 'updated' in (await expect(port,'read',{'path':'same.txt'})).content
                await app.environments.destroy(bindings[0],confirm_destroy=True)
                result=await invoke(port,'read',{'path':'same.txt'})
                assert result.status=='error' and 'env_expired' in result.content
                assert (await app.environments.get(bindings[0])).lifecycle=='destroyed'
                await app.environments.destroy(bindings[1],confirm_destroy=True)
                report.update(reopen='passed',destroy_no_fallback='passed',provider_requests=0)
            # Policy changes are exercised with a new application owner.
            from naiyue_test_agent.sandbox_settings import SandboxNetworkSettings
            settings=settings.model_copy(update={'sandbox':settings.sandbox.model_copy(update={
                'network':SandboxNetworkSettings(mode='external')})})
            async with application() as configured:
                host=await configured.projects.open(project.project_id)
                sid=await host.sessions.create('fake')
                external=await configured.environments.create(project.project_id,sid)
                bindings.append(external.binding_id)
                assert external.settings['network']['mode']=='external'
                assert (await configured.environments.get(bindings[0])).settings['network']['mode']=='none'
                cli=DockerCLI(settings.sandbox,directory/'data')
                interfaces=await run_blocking(cli.run,['container','exec',external.container_id,'python','-I','-c',
                    'import os;assert any(name!="lo" for name in os.listdir("/sys/class/net"));print("external-interface")'])
                assert b'external-interface' in interfaces
                await configured.environments.destroy(external.binding_id,confirm_destroy=True)
                report['external_and_frozen_policy']='passed'
        finally:
            # Settle all records from this disposable registry, including a
            # creation whose response failed before it could enter bindings.
            import sys,logging
            primary=sys.exc_info()[0]
            failures=[]
            async with application() as app:
                rows=await app.environments.list(project.project_id) if project is not None else ()
                for row in rows:
                    try:await app.environments.destroy(row.binding_id,confirm_destroy=True)
                    except Exception as error:
                        failures.append(error)
                        logging.getLogger(__name__).error('sandbox_probe_cleanup_failed error_type=%s',type(error).__name__)
            if failures and primary is None:raise failures[0]
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True)
    parser.add_argument('--image',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='nyt-sandbox-verify-') as directory:
        report=asyncio.run(scenario(Path(directory),args.context,args.image))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
