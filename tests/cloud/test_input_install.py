from pathlib import Path

import pytest

from naiyue_test_agent.cloud_runner.input_install import install_input
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from test_input_formats import package


def test_installed_input_is_independent_and_restart_preserves_changes(tmp_path):
    captured,_,_=package(tmp_path)
    first=tmp_path/'first';second=tmp_path/'second';state1=tmp_path/'state1';state2=tmp_path/'state2'
    for p in (first,second,state1,state2):p.mkdir(mode=0o700)
    for workspace,state in ((first,state1),(second,state2)):
        receipt=install_input(captured.directory,workspace,state,captured.input_id,InputLimits())
        assert receipt['input_id']==captured.input_id and receipt['status']=='installed'
    (first/'project/api.proto').write_bytes(b'agent changes')
    install_input(captured.directory,first,state1,captured.input_id,InputLimits())
    assert (first/'project/api.proto').read_bytes()==b'agent changes'
    assert (second/'project/api.proto').read_bytes()==b'syntax = "proto3";\n'


def test_missing_project_after_install_is_not_replaced(tmp_path):
    import shutil
    captured,_,_=package(tmp_path);work=tmp_path/'work';state=tmp_path/'state';work.mkdir();state.mkdir()
    install_input(captured.directory,work,state,captured.input_id,InputLimits())
    shutil.rmtree(work/'project')
    with pytest.raises(CloudError):install_input(captured.directory,work,state,captured.input_id,InputLimits())
    assert not (work/'project').exists()


def test_lifecycle_reports_input_ready_only_after_backend_install(tmp_path):
    import asyncio
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
    from lifecycle_helpers import Backend,config,register,command
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend=Backend()
            async with open_cloud_backend(runtime,backend=backend):
                task=register(runtime)
                assert (await runtime.lifecycle.command(task.task_id,command(task))).status=='succeeded'
                current=runtime.registry.get_task(task.task_id)
                assert current.input_ready is True
                assert (runtime.settings.data_dir/'tasks'/task.task_id/'workspace/project/fixture.txt').read_text()=='prepared input fixture'
        asyncio.run(run())
