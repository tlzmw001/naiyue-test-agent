"""Disposable two-image acceptance through real application, tools, envd and Docker."""
import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import sys
import tempfile
from unittest.mock import patch
from uuid import uuid4

from scripts.verify_sandbox_inputs import git,seed

logger=logging.getLogger(__name__)


def application(directory,configuration):
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import create_builtin_tools
    settings=AppSettings.model_validate(dict(storage=dict(data_dir=str(directory/'data')),memory=dict(auto_enabled=False),
        permissions=dict(mode='BypassApprovals'),sandbox=configuration))
    return open_service_application('unused',tools=create_builtin_tools(settings.tools),system_prompt='',response_format=None,settings=settings)


def checked_runtime(text,expected):
    versions=dict(re.findall(r'^NYT_(PYTHON|NODE|NPM)=(v?\d+\.\d+\.\d+)\s*$',text,re.M))
    if not text.startswith('exit_code=0;') or any(key not in versions for key in expected):
        raise ValueError('Expected successful commands and explicit runtime versions')
    return versions


def checked_images(cli,references):
    rows=[cli.json(['image','inspect',ref])[0] for ref in references]
    if any(r.get('Os')!='linux' or not isinstance(r.get('Id'),str) or not r['Id'] for r in rows):
        raise ValueError('Two identifiable Linux images are required')
    if len({r['Id'] for r in rows})!=2:raise ValueError('Templates must resolve to different image IDs')
    return rows


async def destroy_owned(api,projects):
    rows=(await api.management())['items']
    if any(row['project_id'] not in projects for row in rows):raise ValueError('Refusing cleanup of a foreign project')
    first=None
    for row in rows:
        try:await api.manage(row['binding_id'],'destroy',confirm_destroy=True)
        except Exception as error:
            logger.error('sandbox_profiles_cleanup_failed error_type=%s',type(error).__name__)
            if first is None:first=error
    if first is not None:raise first


def write_configuration(path,context,python_image,node_image,*,refreshed=False,max_running=2):
    values=dict(context=context,default_profile='node' if refreshed else 'python',
        network=dict(mode='none'),lifecycle=dict(max_running=max_running,max_retained=3,idle_stop_seconds=30,sweep_seconds=3600),
        profiles={'node':dict(name='Node refreshed' if refreshed else 'Node',image=node_image)})
    if not refreshed:values['profiles']['python']=dict(name='Python',image=python_image)
    lines=['[sandbox]',f'context={json.dumps(context)}',f'default_profile={json.dumps(values["default_profile"])}']
    for group in ('network','lifecycle'):
        lines.append('[sandbox.'+group+']')
        lines.extend(f'{key}={json.dumps(value)}' for key,value in values[group].items())
    for key,profile in values['profiles'].items():
        lines.append('[sandbox.profiles.'+key+']')
        lines.extend(f'{field}={json.dumps(value)}' for field,value in profile.items())
    path.write_text('\n'.join(lines)+'\n')


async def scenario(directory,context,python_image,node_image):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage,ToolMessage
    from naiyue_test_agent.agent_runtime.async_utils import run_blocking
    from naiyue_test_agent.contracts.sandbox import SandboxError
    from naiyue_test_agent.contracts.session_environment import SandboxProfileSelection
    from naiyue_test_agent.execution.docker_cli import DockerCLI
    from naiyue_test_agent.execution.docker_environment import DockerEnvironment,verify_container_identity,state_volume
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.sandbox_profiles import load_sandbox_configuration
    from naiyue_test_agent.sandbox_settings import SandboxSettings

    paused={}
    class OfflineModel(FakeMessagesListChatModel):
        def bind_tools(self,tools,**kwargs):return self
        async def _agenerate(self,*args,**kwargs):
            if paused:
                paused['entered'].set();await paused['release'].wait()
            return await super()._agenerate(*args,**kwargs)
    models={key:OfflineModel(responses=[AIMessage(content='done')]) for key in ('python','node')}
    config=directory/'settings.toml';write_configuration(config,context,python_image,node_image)
    initial=load_sandbox_configuration(config)
    cli=DockerCLI(SandboxSettings(context=context),directory)
    daemon=await run_blocking(cli.daemon)
    images=await run_blocking(checked_images,cli,[python_image,node_image])
    report=dict(daemon_id=daemon,image_ids=[r['Id'] for r in images],cases={},provider_requests=0)
    projects=set();bindings=[]
    roots=[]
    for name in ('alpha','beta'):
        folder=directory/name;folder.mkdir();roots.append(seed(folder))
    originals=[dict(head=git(root,'rev-parse','HEAD').decode().strip(),
        index=hashlib.sha256((root/'.git/index').read_bytes()).hexdigest()) for root in roots]

    def passed(name):report['cases'][name]='passed';print('sandbox_profiles_gate stage='+name,flush=True)

    async def call(host,sid,model_id,name,args):
        model=models[model_id];model.i=0
        model.responses=[AIMessage(content='',tool_calls=[dict(name=name,args=args,id=uuid4().hex)]),AIMessage(content='done')]
        result=await host.runs.start(sid,[('user','Exercise the selected sandbox')])
        message=next(m for m in reversed(result['messages']) if isinstance(m,ToolMessage) and m.name==name)
        assert message.status=='success',message.content
        return message.content

    async def refused(code,awaitable):
        try:await awaitable
        except SandboxError as error:assert error.code==code,(code,error.code)
        else:raise AssertionError('Expected refusal: '+code)

    async def read(app,row,path):
        port=await app.environments.files(row.binding_id,project_id=row.project_id,session_id=row.session_id)
        return await run_blocking(port.read_resource,'/workspace/'+path,4096)

    with patch('naiyue_test_agent.application.load_model_catalog',side_effect=lambda _:ModelCatalog('python',models)):
        try:
            async with application(directory,load_sandbox_configuration(config)) as app:
                hosts=[];sessions=[];rows=[]
                for root,model_id in zip(roots,('python','node')):
                    project=await app.projects.add_local(root,trusted=True,operation_id=uuid4().hex)
                    projects.add(project.project_id);host=await app.projects.open(project.project_id);hosts.append(host)
                    selected=app.environments._service.configuration.select(model_id);request=str(uuid4())
                    choice=dict(request_id=request,profile_id=model_id,expected_profile_revision=selected.revision,
                        include_untracked=['.agents/skills/input-check/SKILL.md'] if model_id=='python' else [])
                    sid=await host.sessions.create_sandbox(model_id,**choice);sessions.append(sid)
                    assert await host.sessions.create_sandbox(model_id,**choice)==sid
                    status=await host.sessions.status(sid);assert status['execution']['state']=='ready',status
                    row=await app.environments.get(status['execution']['binding_id']);rows.append(row);bindings.append(row)
                alpha,beta=rows;first,second=sessions;ah,bh=hosts
                assert alpha.image_id==images[0]['Id'] and beta.image_id==images[1]['Id']
                assert alpha.container_id!=beta.container_id and alpha.volume_name!=beta.volume_name
                assert len((await ah.sessions.skills(None,session_id=first)).entries)==1
                assert len((await bh.sessions.skills(None,session_id=second)).entries)==0
                values=await asyncio.gather(call(ah,first,'python','bash',dict(command="printf 'NYT_PYTHON=' && python -c 'import platform; print(platform.python_version())' && printf alpha > same.txt && printf 'alpha update\\n' > app.txt")),
                    call(bh,second,'node','bash',dict(command="printf 'NYT_NODE=' && node --version && printf 'NYT_NPM=' && npm --version && printf beta > same.txt && printf 'beta update\\n' > app.txt")))
                report['runtime_versions']={**checked_runtime(values[0],('PYTHON',)),**checked_runtime(values[1],('NODE','NPM'))}
                assert await read(app,alpha,'same.txt')==b'alpha' and await read(app,beta,'same.txt')==b'beta'
                passed('two_images_formal_tools_files_and_skills_isolated')
                paused.update(entered=asyncio.Event(),release=asyncio.Event())
                active=asyncio.create_task(call(ah,first,'python','read',dict(path='same.txt')))
                try:
                    await asyncio.wait_for(paused['entered'].wait(),30)
                    await refused('env_busy',app.environments.manage(alpha.binding_id,'stop'))
                    await refused('env_busy',app.environments.manage(alpha.binding_id,'destroy',confirm_destroy=True))
                finally:
                    paused['release'].set();await active;paused.clear()
                passed('active_run_rejects_stop_and_destroy')
                third_request=str(uuid4());third=await ah.sessions.create_sandbox('python',request_id=third_request)
                assert (await ah.sessions.status(third))['execution']['error']['code']=='env_running_capacity'
                assert len((await app.environments.management())['items'])==2
                passed('cross_project_running_quota_retains_original_session')

                write_configuration(config,context,python_image,node_image,refreshed=True,max_running=1)
                await app.environments.refresh(config)
                assert (await app.environments.management())['usage']==dict(running=2,retained=2)
                old=app.environments._service.configuration
                assert (await app.environments.get(alpha.binding_id)).image_id==alpha.image_id
                assert (await app.environments.get(beta.binding_id)).profile_name=='Node'
                stale_request=str(uuid4())
                await refused('env_profile_changed',bh.sessions.create_sandbox('node',request_id=stale_request,
                    profile_id='node',expected_profile_revision=SandboxProfileSelection.from_record(beta).revision))
                config.write_text('invalid TOML')
                await refused('env_configuration_invalid',app.environments.refresh(config))
                assert app.environments._service.configuration is old
                write_configuration(config,context,python_image,node_image,refreshed=True)
                await app.environments.refresh(config)
                passed('hot_reload_and_lower_quota_do_not_change_or_stop_existing_bindings')

                snapshot=await ah.sessions.capture_result(first,result_id=uuid4().hex,include_untracked=['same.txt'])
                assert snapshot['state']=='ready'
                task=json.loads(await call(ah,first,'python','bash',dict(command='printf background-ready; while :; do sleep 1; done',background=True)))['task_id']
                records=app.environments._service.records()
                current=await app.environments.get(alpha.binding_id)
                await run_blocking(records.save,current.changed(last_used_at=100.0))
                await app.environments.sweep_idle(now=200.0)
                assert (await app.environments.get(alpha.binding_id)).lifecycle=='running'
                assert not (await ah.tasks.output(task,session_id=first))['stale']
                assert (await ah.tasks.stop(task,session_id=first))['cleanup_confirmed']
                # The observer can still hold a read lease after stop returns. Wait for its real settlement.
                await asyncio.wait_for(asyncio.shield(app.environments._operations._watchers[(alpha.binding_id,task)]),30)
                current=await app.environments.get(alpha.binding_id)
                await app.environments.sweep_idle(now=current.last_used_at+31)
                assert (await app.environments.get(alpha.binding_id)).lifecycle=='stopped'
                await app.environments.start(alpha.binding_id)
                assert await read(app,alpha,'same.txt')==b'alpha'
                passed('real_background_protects_idle_and_idle_stop_keeps_files')

                await ah.sessions.select_permission_mode(first,'Default')
                model=models['python'];model.i=0;model.responses=[AIMessage(content='',tool_calls=[dict(name='write',args=dict(path='.approval.txt',content='approved'),id=uuid4().hex)]),AIMessage(content='done')]
                await ah.runs.start(first,[('user','Exercise pending approval')]);assert ah.runs.pending_tools(first)
                current=await app.environments.get(alpha.binding_id);await run_blocking(records.save,current.changed(last_used_at=100.0))
                await app.environments.sweep_idle(now=200.0)
                assert (await app.environments.get(alpha.binding_id)).lifecycle=='running'
                await ah.runs.resume_approval(first,['approve']);await ah.sessions.select_permission_mode(first,'BypassApprovals')
                passed('pending_approval_blocks_idle_stop')
                await run_blocking(records.save,beta.changed(lifecycle='unknown'))
                assert (await app.environments.management())['usage']['running']==2
                await app.environments.sweep_idle(now=200.0)
                assert (await app.environments.get(beta.binding_id)).lifecycle=='unknown'
                await app.environments.recover(beta.binding_id)
                passed('unknown_state_keeps_capacity_until_original_resource_is_checked')

                await app.environments.stop(beta.binding_id)
                assert await ah.sessions.create_sandbox('python',request_id=third_request)==third
                third_status=await ah.sessions.prepare_environment(third);assert third_status['state']=='ready'
                gamma=await app.environments.get(third_status['binding_id']);bindings.append(gamma)
                assert gamma.profile_id=='python' and gamma.image_id==alpha.image_id
                fourth=await bh.sessions.create_sandbox('node',request_id=str(uuid4()))
                assert (await bh.sessions.status(fourth))['execution']['error']['code']=='env_retained_capacity'
                await refused('env_confirmation_required',app.environments.manage(alpha.binding_id,'destroy'))
                await app.environments.manage(alpha.binding_id,'destroy',confirm_destroy=True)
                assert (await ah.sessions.result(first,snapshot['result_id']))['state']=='ready'
                assert '+alpha update' in (await ah.sessions.result_diff(first,snapshot['result_id'],'app.txt'))['patch']
                fourth_status=await bh.sessions.prepare_environment(fourth);assert fourth_status['state']=='ready'
                delta=await app.environments.get(fourth_status['binding_id']);bindings.append(delta)
                assert delta.profile_id=='node' and delta.profile_name=='Node refreshed' and delta.image_id==beta.image_id
                await refused('env_running_capacity',app.environments.start(beta.binding_id))
                passed('original_preparation_retained_quota_destroy_and_saved_results')
                closing_task=json.loads(await call(ah,third,'python','bash',dict(command='printf retained > closing.txt; while :; do sleep 1; done',background=True)))['task_id']
                for root,original in zip(roots,originals):
                    assert git(root,'rev-parse','HEAD').decode().strip()==original['head']
                    assert hashlib.sha256((root/'.git/index').read_bytes()).hexdigest()==original['index']
                    assert (root/'app.txt').read_text()=='working input\n' and not (root/'same.txt').exists()
            for row in (beta,gamma,delta):
                data=await run_blocking(cli.container,row.container_id)
                assert data is not None and not data['State']['Running']
                assert await run_blocking(cli.volume,row.volume_name) and await run_blocking(cli.volume,state_volume(row))
            passed('service_exit_stops_original_containers_and_preserves_both_volumes')

            async with application(directory,load_sandbox_configuration(config)) as app:
                ah=await app.projects.open(alpha.project_id)
                assert (await app.environments.get(gamma.binding_id)).container_id==gamma.container_id
                await app.environments.start(gamma.binding_id)
                await ah.sessions.prepare(third)
                assert await read(app,gamma,'closing.txt')==b'retained'
                assert (await ah.tasks.output(closing_task,session_id=third))['cleanup_confirmed']
                passed('reopen_explicit_start_uses_original_container_files_and_task_archive')
                actual_request=DockerEnvironment.request;submissions=[]
                def lose_ack(engine,row,operation,params,**kwargs):
                    value=actual_request(engine,row,operation,params,**kwargs)
                    if row.binding_id==gamma.binding_id and operation=='operation_submit' and params['kind']=='shell':
                        submissions.append(params['operation_id'])
                        if len(submissions)==1:raise SandboxError('env_operation_unknown')
                    return value
                with patch.object(DockerEnvironment,'request',lose_ack):
                    await call(ah,third,'python','bash',dict(command='printf x >> once.txt; cat once.txt'))
                assert len(submissions)==1 and await read(app,gamma,'once.txt')==b'x'
                passed('real_rpc_lost_ack_queries_original_operation_without_replay')
                await app.environments.stop(gamma.binding_id)
                await refused('env_stopped',ah.runs.start(third,[('user','Must refuse stopped environment')]))
                await app.environments.start(gamma.binding_id)
                verify_container_identity(gamma,await run_blocking(cli.container,gamma.container_id))
                await run_blocking(cli.run,['container','rm','--force',gamma.container_id],mutates=True)
                await refused('env_expired',ah.runs.start(third,[('user','Must refuse missing environment')]))
                await app.environments.manage(gamma.binding_id,'destroy',confirm_destroy=True)
                assert not (roots[0]/'once.txt').exists() and not (roots[0]/'closing.txt').exists()
                passed('stopped_and_missing_original_environment_never_fall_back_to_host')
                await destroy_owned(app.environments,projects)
                assert (await app.environments.management())['usage']==dict(running=0,retained=0)
                assert (await ah.history.session(first))['session_id']==first
                assert (await ah.sessions.result(first,snapshot['result_id']))['state']=='ready'
            unavailable=DockerCLI(SandboxSettings(context='nyt-unavailable-'+uuid4().hex),directory)
            await refused('env_unreachable',run_blocking(unavailable.daemon))
            passed('unavailable_docker_context_is_explicit_and_does_not_change_default')
        finally:
            primary=sys.exc_info()[0]
            try:
                async with application(directory,initial) as app:await destroy_owned(app.environments,projects)
            except BaseException as error:
                logger.error('sandbox_profiles_final_cleanup_failed error_type=%s',type(error).__name__)
                if primary is None:raise
    for row in bindings:
        assert await run_blocking(cli.container,row.container_id) is None
        assert await run_blocking(cli.volume,row.volume_name) is None
        assert await run_blocking(cli.volume,state_volume(row)) is None
    report['bindings']=[dict(binding_id=r.binding_id,project_id=r.project_id,session_id=r.session_id,image_id=r.image_id,
        container_id=r.container_id,volume_name=r.volume_name,state_volume=state_volume(r)) for r in bindings]
    passed('all_owned_containers_and_both_volumes_removed')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True);parser.add_argument('--python-image',required=True)
    parser.add_argument('--node-image',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    folder=tempfile.mkdtemp(prefix='nyt-profile-gate-')
    try:
        report=asyncio.run(scenario(Path(folder).resolve(),args.context,args.python_image,args.node_image))
    except BaseException:
        logger.error('sandbox_profiles_recovery_directory path=%s',folder)
        raise
    else:shutil.rmtree(folder)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
