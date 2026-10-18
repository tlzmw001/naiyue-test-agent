"""Service identity is a lightweight, authenticated read of the bound settings."""

import asyncio
from pathlib import PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest

from naiyue_test_agent.tools import ToolSettings
from naiyue_test_agent.web.service import WebService
from test_async_application import StreamingToolModel
from test_web_api import web_client


@pytest.mark.parametrize('bound', [False, True])
def test_service_info_reads_binding_without_session_or_model_access(monkeypatch, tmp_path, bound):
    async def scenario():
        workspace = tmp_path / '项目与代码'
        workspace.mkdir()
        model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, model, runtime_settings=ToolSettings(),
                              workspace_root=workspace if bound else None) as (client, web):
            service = web.state.service
            original = service.application
            service.application = object()  # Metadata must not ask the application to load anything.
            try:
                response = await client.get('/api/service')
            finally:
                service.application = original
            assert response.status_code == 200
            assert response.json() == {'workspace_root': str(workspace.resolve()) if bound else None,
                                       'workspace_label': workspace.name if bound else None}
            assert (await client.get('/api/sessions')).json() == []
            assert model.seen == []
    asyncio.run(scenario())


def test_service_info_retains_auth_host_and_origin_checks(monkeypatch, tmp_path):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]),
                              runtime_settings=ToolSettings(), workspace_root=tmp_path) as (client, _):
            for headers, status in [({'Authorization': ''}, 401),
                                    ({'Host': 'untrusted.example'}, 403),
                                    ({'Origin': 'https://untrusted.example'}, 403)]:
                response = await client.get('/api/service', headers=headers)
                assert response.status_code == status
                assert str(tmp_path) not in response.text
                assert 'workspace_root' not in response.text
    asyncio.run(scenario())


def test_service_info_uses_canonical_startup_binding(monkeypatch, tmp_path):
    async def scenario():
        workspace = tmp_path / '中文工作区'
        workspace.mkdir()
        given = workspace / '..' / workspace.name
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]),
                              runtime_settings=ToolSettings(), workspace_root=given) as (client, _):
            assert (await client.get('/api/service')).json() == {
                'workspace_root': str(workspace.resolve()), 'workspace_label': workspace.name}
    asyncio.run(scenario())


@pytest.mark.parametrize('path,label', [(PurePosixPath('/'), '/'),
    (PureWindowsPath('C:/'), 'C:\\'), (PureWindowsPath('//server/share/'), '\\\\server\\share\\'),
    (PureWindowsPath('C:/work/project'), 'project')])
def test_service_info_names_filesystem_roots_on_both_path_flavors(path, label):
    service = WebService(object(), object(), SimpleNamespace(workspace_root=path))
    assert service.service_info() == {'workspace_root': str(path), 'workspace_label': label}
