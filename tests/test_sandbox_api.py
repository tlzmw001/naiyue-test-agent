"""The service exposes the backend without loading models or probing Docker."""
import asyncio
from dataclasses import replace
from pathlib import Path
import pytest
from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.contracts.sandbox import SandboxError,SandboxRecord
from naiyue_test_agent.settings import AppSettings


def test_service_environment_api_is_lazy_and_checks_session_owner(monkeypatch,tmp_path):
    from naiyue_test_agent.execution.docker_cli import DockerCLI
    def forbidden(*args,**kwargs):raise AssertionError('Unexpected Docker access')
    monkeypatch.setattr(DockerCLI,'daemon',forbidden)
    async def scenario():
        settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')}})
        async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
            root=tmp_path/'project';root.mkdir()
            project=await app.projects.add_local(root,trusted=True,operation_id='P')
            assert await app.environments.list(project.project_id)==()
            with pytest.raises(SandboxError,match='env_session_mismatch'):
                await app.environments.create(project.project_id,'not-a-session')
            assert app._lifetime._catalog.value is None
    asyncio.run(scenario())


def test_frozen_records_do_not_share_mutable_settings_with_backend(tmp_path):
    from contextlib import closing
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
    with closing(ProjectRegistry(tmp_path/'registry.sqlite')) as registry:
        repo=EnvironmentRegistry(registry.database)
        row=SandboxRecord('B','P','S','running','D','container','volume','image',{'network':{'mode':'none'}})
        repo.reserve(row)
        row.settings['network']['mode']='external'
        loaded=repo.get('B')
        assert loaded.settings['network']['mode']=='none'
        loaded.settings['network']['mode']='external'
        assert repo.get('B').settings['network']['mode']=='none'
