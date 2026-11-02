"""B task grants and one Python environment across real Shell/Kit calls."""
import asyncio
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from uuid import uuid4
import zipfile

from langchain_core.messages import AIMessage
import pytest
from pydantic import ValidationError

from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools.permission_rules import PermissionRule, PermissionSettings
from cloud_helpers import settings
from lifecycle_helpers import config
from test_async_application import StreamingToolModel, call
from test_runner_kernel import bootstrap, request


def test_cloud_and_private_bootstrap_default_to_authorized_no_prompt_tools(tmp_path):
    policy=settings(tmp_path).runner_permissions
    assert policy.mode=='NoAsk'
    assert policy.allow==(PermissionRule(tool='*'),)
    assert bootstrap().permissions==policy


@pytest.mark.parametrize('mode',['Default','Auto','BypassApprovals'])
def test_product_cloud_configuration_rejects_interactive_or_bypass_modes(tmp_path,mode):
    # Auto itself needs valid reviewer config so this checks the B boundary.
    value={'mode':mode}
    if mode=='Auto':value['auto']={'fast_model_id':'A','detailed_model_id':'A'}
    with pytest.raises(ValidationError,match='cloud_requires_noask'):
        settings(tmp_path,runner_permissions=value)
    with pytest.raises(ValidationError,match='cloud_requires_noask'):
        type(bootstrap()).model_validate(bootstrap().model_dump()|{'permissions':value})


def test_default_run_executes_workspace_changes_without_approval_and_deduplicates(tmp_path):
    async def scenario():
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        model=StreamingToolModel(replies=[call('write',{'path':'answer.txt','content':'42'}),
            call('bash',{'command':'printf executed > execution.txt'}),AIMessage(content='saved')])
        cmd=request()
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as runner:
            result=await runner.execute(cmd)
            assert result['status']=='completed'
            assert (root/'answer.txt').read_text()=='42'
            assert (root/'execution.txt').read_text()=='executed'
            assert not runner.sessions.require(runner.bootstrap.task_id).describe_session()['pending_approval']
            assert len(model.seen)==3
            assert await runner.execute(cmd)==result and len(model.seen)==3
            assert runner.journal.pending()[-1].payload['record']=='run_finished'
    asyncio.run(scenario())


def test_explicit_deny_remains_effective_without_an_approval_channel(tmp_path):
    async def scenario():
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        b=bootstrap();policy=b.permissions.model_copy(update={'deny':(PermissionRule(tool='bash'),)})
        model=StreamingToolModel(replies=[call('bash',{'command':'printf wrong > forbidden.txt'}),AIMessage(content='denied')])
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model}),permissions=policy) as runner:
            assert (await runner.execute(request()))['status']=='completed'
            assert not (root/'forbidden.txt').exists()
            tool=model.seen[-1][-1]
            assert tool.type=='tool' and tool.status=='error'
            assert not runner.sessions.require(b.task_id).describe_session()['pending_approval']
    asyncio.run(scenario())


@pytest.mark.parametrize('values',[{'PATH':'/untrusted'},{'PYTHONPATH':'/untrusted'},
    {'PYTHONHOME':'/untrusted'},{'VIRTUAL_ENV':'/untrusted'},{'BASH_ENV':'/untrusted'},
    {'BAD=NAME':'value'},{'HTTP_PROXY':'bad\0value'}])
def test_tool_environment_rejects_interpreter_overrides_and_invalid_values(tmp_path,values):
    with pytest.raises(ValidationError):settings(tmp_path,tool_environment={'values':values})


def test_explicit_tool_environment_reaches_private_bootstrap_without_parent_mutation(tmp_path,monkeypatch):
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from test_runner_control import prepared
    monkeypatch.setenv('NYT_COORDINATOR_ONLY','never-copy-to-task')
    configured=config(tmp_path,tool_environment={'values':{'PIP_INDEX_URL':'https://packages.example.invalid/simple'}})
    with open_cloud_runtime(configured) as runtime:
        b=prepared(runtime)
        assert b.tool_environment.values=={'PIP_INDEX_URL':'https://packages.example.invalid/simple'}
        assert 'NYT_COORDINATOR_ONLY' not in b.tool_environment.values
        assert type(b).model_validate_json(b.model_dump_json()).tool_environment==b.tool_environment
    assert os.environ['NYT_COORDINATOR_ONLY']=='never-copy-to-task'


def test_project_venv_created_after_start_is_shared_by_shell_and_kit(tmp_path):
    # Synthetic wheel: verifies interpreter/argv wiring, not actual AITest behavior.
    root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
    wheel=tmp_path/'aitest_kit-0.0.1-py3-none-any.whl'
    with zipfile.ZipFile(wheel,'w') as archive:
        archive.writestr('aitest_kit/__init__.py','MARKER=143\n')
        archive.writestr('aitest_kit/cli.py','import json,sys,os\nfrom pathlib import Path\n'
            'def main():\n Path("kit-python.json").write_text(json.dumps({"python":sys.executable,"marker":os.getenv("TASK_MARKER")}))\n return 0\n'
            'if __name__=="__main__":raise SystemExit(main())\n')
        archive.writestr('aitest_kit-0.0.1.dist-info/METADATA','Metadata-Version: 2.1\nName: aitest-kit\nVersion: 0.0.1\n')
        archive.writestr('aitest_kit-0.0.1.dist-info/WHEEL','Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr('aitest_kit-0.0.1.dist-info/RECORD','')
    command=(f'{shlex.quote(sys.executable)} -m venv .venv && '
        f'.venv/bin/python -m pip install --no-index --no-deps {shlex.quote(str(wheel))}')
    script='import sys,json,aitest_kit;from pathlib import Path;assert aitest_kit.MARKER==143;Path("shell-python.json").write_text(json.dumps({"python":sys.executable}))'
    async def scenario():
        from naiyue_test_agent.contracts.cloud_runner import RunnerToolEnvironment
        b=bootstrap().model_copy(update={'tool_environment':RunnerToolEnvironment(values={'TASK_MARKER':'task-only'})})
        model=StreamingToolModel(replies=[call('bash',{'command':command}),
            call('bash',{'command':'python -c '+shlex.quote(script)}),
            call('aitest_kit',{'args':['doctor']}),AIMessage(content='finished')])
        parent=dict(os.environ);runner_python=sys.executable
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model})) as runner:
            assert (await runner.execute(request()))['status']=='completed'
            assert runner.journal.pending()[-1].payload['record']=='run_finished'
        assert len(model.seen)==4
        expected=str(root/'.venv/bin/python')
        assert json.loads((root/'shell-python.json').read_text())=={'python':expected}
        assert json.loads((root/'kit-python.json').read_text())=={'python':expected,'marker':'task-only'}
        assert os.environ==parent and sys.executable==runner_python
    asyncio.run(scenario())


@pytest.mark.parametrize('workspace_shadow',[False,True])
def test_missing_kit_in_selected_project_python_is_a_tool_failure(tmp_path,workspace_shadow):
    root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
    subprocess.run([sys.executable,'-m','venv','--without-pip',str(root/'.venv')],check=True,capture_output=True)
    if workspace_shadow:
        (root/'aitest_kit').mkdir()
        (root/'aitest_kit/__init__.py').write_text('')
        (root/'aitest_kit/cli.py').write_text('from pathlib import Path;Path("shadow-executed").touch()')
    async def scenario():
        model=StreamingToolModel(replies=[call('aitest_kit',{'args':['doctor']}),AIMessage(content='missing dependency')])
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as runner:
            await runner.execute(request())
        tool=model.seen[-1][-1]
        assert tool.type=='tool' and tool.status=='error'
        assert 'aitest_kit.cli' in tool.content and 'No module named' in tool.content
        assert not (root/'shadow-executed').exists()
    asyncio.run(scenario())


@pytest.mark.parametrize('broken',['missing','not_executable'])
def test_broken_project_python_is_reported_without_base_kit_fallback(tmp_path,broken):
    root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
    (root/'.venv/bin').mkdir(parents=True)
    if broken=='not_executable':
        (root/'.venv/bin/python').write_text('not executable')
        (root/'.venv/bin/python').chmod(0o600)
    async def scenario():
        model=StreamingToolModel(replies=[call('aitest_kit',{'args':['doctor']}),
            call('bash',{'command':'printf repairable > repair.txt'}),AIMessage(content='repair needed')])
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as runner:
            await runner.execute(request())
        tool=model.seen[1][-1]
        assert tool.type=='tool' and tool.status=='error'
        assert 'project_python_unavailable' in tool.content
        assert (root/'repair.txt').read_text()=='repairable'
    asyncio.run(scenario())
