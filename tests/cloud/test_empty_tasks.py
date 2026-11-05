"""A task can prepare and run without a synthetic source package or Git baseline."""
import asyncio
from pathlib import Path
from uuid import uuid4
import pytest
from lifecycle_helpers import config,template
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CreateProject,CloudError


def test_empty_task_is_gated_by_real_template_capability(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        project=runtime.commands.create_project(CreateProject(command_id=str(uuid4()),name='empty'))
        request=dict(command_id=str(uuid4()),project_id=project.resource_id,template_id='python-tests')
        with pytest.raises(CloudError,match='template_files_update_required'):runtime.inputs.create_task(request)
        ready=template(file_transfer_version=1)
        runtime.settings=runtime.settings.model_copy(update={'templates':{'python-tests':ready}})
        receipt=runtime.inputs.create_task(request)
        task=runtime.registry.get_task(receipt.resource_id)
        assert task.input_id is None and task.lifecycle=='creating'
        assert runtime.inputs.create_task(request)==receipt
        from naiyue_test_agent.application.cloud.directories import TaskDirectories
        mounts=TaskDirectories(runtime.settings.data_dir).prepare(task,ready)
        assert '/input' not in [mount.destination for mount in mounts]
        plan,_=runtime.results.reserve_run(task,str(uuid4()),uuid4().hex)
        assert plan.input_id is None


def test_empty_install_is_idempotent_and_does_not_clear_later_files(tmp_path):
    from naiyue_test_agent.cloud_runner.input_install import install_empty
    work=tmp_path/'workspace';state=tmp_path/'state';work.mkdir();state.mkdir()
    assert install_empty(work,state)=={'input_id':None,'status':'installed'}
    (work/'project'/'keep.txt').write_text('agent work')
    assert install_empty(work,state)['status']=='installed'
    assert (work/'project'/'keep.txt').read_text()=='agent work'


def test_empty_install_rejects_foreign_workspace(tmp_path):
    from naiyue_test_agent.cloud_runner.input_install import install_empty
    work=tmp_path/'workspace';state=tmp_path/'state';work.mkdir();state.mkdir()
    (work/'foreign').write_text('keep')
    with pytest.raises(CloudError):install_empty(work,state)
    assert (work/'foreign').read_text()=='keep'


def test_local_preparation_without_source_never_uploads(tmp_path):
    from test_manual_task_preparation import setup
    async def scenario():
        manager,domain,store,project,sid,remote=setup(tmp_path)
        try:
            request=dict(command_id=str(uuid4()),local_session_id=sid,profile_id='work',template_id='test')
            result=await manager.submit(domain,'project',request);await manager.drain()
            binding=store.cloud_tasks.binding(result['binding']['binding_id'],sid)
            assert binding['preparation']=='prepared' and binding['input_id'] is None
            assert remote.uploads==[]
        finally:
            await manager.close();store.close()
    asyncio.run(scenario())


def test_empty_task_freezes_only_output_without_git_or_input(tmp_path):
    from naiyue_test_agent.contracts.cloud_results import ResultPlan,ResultBoundary,ResultLimits
    from naiyue_test_agent.contracts.cloud_inputs import InputLimits
    from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result,verify_spool
    plan=ResultPlan(result_id=str(uuid4()),source_command_id=str(uuid4()),source_run_id=uuid4().hex,
        task_id=uuid4().hex,project_id=str(uuid4()),input_id=None,source_generation=1,output_directory='.nyt-output')
    boundary=ResultBoundary(kind='run',epoch=str(uuid4()),seq=1,outcome='completed')
    root=tmp_path/'work';root.mkdir();(root/'not-an-output').write_text('do not collect')
    report=root/plan.output_directory/plan.source_run_id;report.mkdir(parents=True);(report/'report.txt').write_text('done')
    target=tmp_path/'spool'
    freeze_result(root,tmp_path/'missing-input',target,plan,boundary,1,ResultLimits(git_executable='not-installed'),InputLimits())
    state=verify_spool(target,plan,boundary,ResultLimits())
    assert [(f.role,f.artifact.relative_name) for f in state.bundle.files]==[('artifact','report.txt')]
    assert (target/'files'/state.bundle.files[0].artifact.artifact_id).read_bytes()==b'done'


def test_empty_task_runs_and_commits_output_via_real_protocol(tmp_path):
    from naiyue_test_agent.application.cloud.directories import TaskDirectories
    from test_runner_control import client,connect,hello
    from test_results import finish,run_body
    from test_result_api import transfer
    from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result,verify_spool
    cfg=config(tmp_path).model_copy(update={'templates':{'python-tests':template(file_transfer_version=1)}})
    with open_cloud_runtime(cfg) as r:
        project=r.commands.create_project(CreateProject(command_id=str(uuid4()),name='empty'))
        receipt=r.inputs.create_task(dict(command_id=str(uuid4()),project_id=project.resource_id,template_id='python-tests'))
        task=r.registry.get_task(receipt.resource_id);TaskDirectories(cfg.data_dir).prepare(task,task.template_snapshot)
        b=r.control.prepare(task.task_id);task=r.registry.get_task(task.task_id)
        r.replace_task(task.model_copy(update={'revision':task.revision+1,'environment_phase':'backend_ready','input_ready':True,
            'sandbox_id':'fixture-sandbox','lifecycle':'starting'}),expected_revision=task.revision,expected_generation=task.generation)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b)|{'file_transfer_version':1});ws.receive_json()
            command,_=finish(r,b,c,ws,run_body(r.registry.get_task(b.task_id)))
            rid=command['result_plan']['result_id'];raw=b.token.get_secret_value();record=r.results.begin(raw,b.task_id,rid)
            work=tmp_path/'work';reports=work/record.plan.output_directory/record.plan.source_run_id;reports.mkdir(parents=True)
            (reports/'report.txt').write_text('report')
            spool=tmp_path/'spool';freeze_result(work,tmp_path/'missing',spool,record.plan,record.boundary,record.generation,r.settings.result_limits,r.settings.input_limits)
            state=verify_spool(spool,record.plan,record.boundary,r.settings.result_limits)
            r.results.prepare(raw,b.task_id,rid,state.bundle.model_dump(mode='json'));transfer(r,b,c,spool,state)
            result=r.results.commit(raw,b.task_id,rid)
            assert result.phase=='available' and result.plan.input_id is None
            assert [file.artifact.relative_name for file in result.bundle.files]==['report.txt']


def test_output_collects_hidden_and_opaque_files_but_not_links(tmp_path):
    from naiyue_test_agent.contracts.cloud_results import ResultPlan,ResultBoundary,ResultLimits
    from naiyue_test_agent.contracts.cloud_inputs import InputLimits
    from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result,verify_spool
    plan=ResultPlan(result_id=str(uuid4()),source_command_id=str(uuid4()),source_run_id=uuid4().hex,
        task_id=uuid4().hex,project_id=str(uuid4()),input_id=None,source_generation=1,output_directory='.nyt-output')
    boundary=ResultBoundary(kind='run',epoch=str(uuid4()),seq=1,outcome='completed')
    root=tmp_path/'work';report=root/plan.output_directory/plan.source_run_id;report.mkdir(parents=True)
    (report/'.env').write_bytes(b'opaque secret-like user content')
    (report/'.git').mkdir();(report/'.git'/'config').write_bytes(b'opaque')
    dest=tmp_path/'spool';freeze_result(root,tmp_path/'none',dest,plan,boundary,1,ResultLimits(),InputLimits())
    state=verify_spool(dest,plan,boundary,ResultLimits())
    assert {f.artifact.relative_name for f in state.bundle.files}=={'.env','.git/config'}
    (report/'link').symlink_to(report/'.env')
    with pytest.raises(CloudError,match='result_artifact_type'):
        freeze_result(root,tmp_path/'none',tmp_path/'bad-spool',plan,boundary,1,ResultLimits(),InputLimits())
