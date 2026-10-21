"""Default paths and Read settings are consumed by real application entries."""

from naiyue_test_agent import settings as application_settings

from native_application_helpers import async_test

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage


@pytest.mark.parametrize('names', [[], ['powershell', 'read']])
def test_cli_wires_configured_default_tools_without_model_requests(monkeypatch, tmp_path, names):
    from naiyue_test_agent import application
    from naiyue_test_agent.application import cli
    from naiyue_test_agent.model_access import ModelCatalog
    from test_application_workspace import ToolCapableFake
    root = tmp_path / 'settings'
    root.mkdir()
    (root / 'settings.toml').write_text('default_active_tools = ' + json.dumps(names))
    monkeypatch.setattr(application_settings, 'get_agent_dir', lambda: root)
    model = ToolCapableFake(responses=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    observed = []
    original = cli.build_local_application
    def build(*args, **kwargs):
        observed.append(tuple(kwargs['initial_active_tools']))
        return original(*args, **kwargs)
    monkeypatch.setattr(cli, 'build_local_application', build)
    monkeypatch.setattr('builtins.input', lambda *_: ':quit')
    assert cli.main(['--no-session', '--workspace', str(tmp_path)]) == 0
    assert observed == [tuple(names)]


@pytest.mark.parametrize('initial, expected', [(None, ('read',)), ([], ()), (['read'], ('read',))])
@async_test
async def test_sdk_tool_parameter_keeps_its_existing_precedence(monkeypatch, tmp_path, initial, expected):
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_application import build_async_local_application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import create_session_read_tool
    from test_async_application import StreamingToolModel
    model = StreamingToolModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = await build_async_local_application('unused', tools=[create_session_read_tool()], system_prompt='',
        response_format=None, settings=AppSettings(default_active_tools=['powershell']), initial_active_tools=initial)
    try:
        sid = await app.acreate_workspace_session('A', tmp_path)
        assert app.get_active_tools(sid) == expected
        assert model.seen == []
    finally:
        await app.aclose()


@async_test
async def test_model_catalog_reads_the_default_user_file(monkeypatch, tmp_path):
    from naiyue_test_agent import model_access
    from naiyue_test_agent.model_access import load_model_catalog, ModelConfigError
    from test_model_access import config_for_all_kinds
    from test_application_workspace import ToolCapableFake
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    root = tmp_path / '.naiyue-test-agent'
    root.mkdir()
    path = root / 'models.json'
    path.write_text(json.dumps(config_for_all_kinds()))
    path.chmod(0o600)
    read_text = Path.read_text
    def read_synthetic(source, *args, **kwargs):
        if source != path:
            raise AssertionError('Only the synthetic model file may be read')
        return read_text(source, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read_synthetic)
    # This case exercises file selection and validation; SDK construction has its own tests.
    constructed = []
    def build(*args, **kwargs):
        constructed.append(args[3])
        return ToolCapableFake(responses=[])
    monkeypatch.setattr(model_access, '_build_model', build)
    catalog = load_model_catalog()
    assert catalog.default_model_id == 'gateway'
    assert constructed == ['official', 'gateway', 'claude', 'local']
    await catalog.aclose()
    path.write_text('invalid')
    with pytest.raises(ModelConfigError, match='invalid JSON'):
        load_model_catalog()


@pytest.mark.parametrize('content, expected', [
    ('[tools.read]\ndefault_limit=1\n', 'first\n[read: lines 1-1; next_offset=2]\n[read: stopped by 1 line page limit; continue with the same path, offset=2, limit=1]'),
    ('INVALID = [', None),
])
def test_cli_loads_read_settings_and_default_model_path(monkeypatch, tmp_path, content, expected):
    from naiyue_test_agent import application
    from naiyue_test_agent.application import cli
    from naiyue_test_agent.tools import settings as module
    from naiyue_test_agent.model_access import ModelCatalog
    from test_application_workspace import ToolCapableFake
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    root = tmp_path / '.naiyue-test-agent'
    root.mkdir()
    (root / 'settings.toml').write_text(content)
    monkeypatch.setattr(application_settings, 'get_agent_dir', lambda: root)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'doc.md').write_text('first\nsecond\n')
    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{'name': 'read', 'args': {'path': 'doc.md'}, 'id': 'read'}]),
        AIMessage(content='done'),
    ])
    loaded = []
    def load(path):
        loaded.append(path)
        return ModelCatalog('A', {'A': model})
    # Inspect the real model's second request, rather than substituting the application.
    from pydantic import Field
    class Recording(ToolCapableFake):
        seen: list = Field(default_factory=list)
        def _generate(self, messages, *args, **kwargs):
            self.seen.append(messages)
            return super()._generate(messages, *args, **kwargs)
    model = Recording(responses=model.responses)
    monkeypatch.setattr(application, 'load_model_catalog', load)
    lines = iter(['read', ':quit'])
    monkeypatch.setattr('builtins.input', lambda *_: next(lines))
    status = cli.main(['--no-session', '--workspace', str(workspace)])
    if expected is None:
        assert status == 1 and loaded == [] and model.seen == []
        return
    assert status == 0
    assert loaded == [root / 'models.json']
    feedback = next(m for m in model.seen[1] if isinstance(m, ToolMessage))
    assert feedback.status == 'success' and feedback.content == expected


@pytest.mark.parametrize('content, expected', [
    ('[tools.read]\ndefault_limit=1\n', 'first\n[read: lines 1-1; next_offset=2]\n[read: stopped by 1 line page limit; continue with the same path, offset=2, limit=1]'),
    ('INVALID = [', None),
])
@async_test
async def test_web_uses_the_same_settings_loader(monkeypatch, tmp_path, content, expected):
    from test_web_api import web_client, request
    from test_async_application import StreamingToolModel, call
    from naiyue_test_agent.tools import settings as module
    root = tmp_path / 'agent-settings'
    root.mkdir()
    (root / 'settings.toml').write_text(content)
    monkeypatch.setattr(application_settings, 'get_agent_dir', lambda: root)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'doc.md').write_text('first\nsecond\n')
    async def scenario():
        model = StreamingToolModel(replies=[call('read', {'path': 'doc.md'}), AIMessage(content='done')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=workspace) as (client, app):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('read'))).json()['run_id']
            await client.get(f'/api/runs/{run}/events')
            feedback = next(m for m in model.seen[1] if isinstance(m, ToolMessage))
            assert feedback.content == expected
    if expected is None:
        with pytest.raises(ValueError, match='Permission settings could not be read or parsed'):
            await scenario()
    else:
        await scenario()


def test_web_entry_model_config_is_optional(monkeypatch, tmp_path):
    from naiyue_test_agent.web import __main__ as entry
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    (tmp_path / 'index.html').write_text('<html>fixture</html>')
    seen = []
    monkeypatch.setattr(entry, 'create_web_app', lambda settings: seen.append(settings))
    def stop(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(entry.uvicorn.Server, 'run', stop)
    assert entry.main(['--frontend-dist', str(tmp_path)]) == 130
    assert len(seen) == 1
    assert seen[0].config_path == tmp_path / '.naiyue-test-agent' / 'models.json'


@async_test
async def test_two_applications_keep_their_own_read_budget(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import ReadSettings, create_session_read_tool
    from test_application_run_events import make_app, ToolModel
    from test_async_application import call
    (tmp_path / 'doc.md').write_text('a\nb\nc\n')
    first = await make_app(monkeypatch,
        ToolModel(responses=[call('read', {'path': 'doc.md'}), AIMessage(content='done')]),
        [create_session_read_tool(settings=ReadSettings(default_limit=1))])
    second = await make_app(monkeypatch,
        ToolModel(responses=[call('read', {'path': 'doc.md'}), AIMessage(content='done')]),
        [create_session_read_tool(settings=ReadSettings(default_limit=3))])
    first_result = await first.arun(await first.acreate_workspace_session('A', tmp_path), [('user', 'read')])
    second_result = await second.arun(await second.acreate_workspace_session('A', tmp_path), [('user', 'read')])
    assert next(m for m in first_result['messages'] if isinstance(m, ToolMessage)).content == 'a\n[read: lines 1-1; next_offset=2]\n[read: stopped by 1 line page limit; continue with the same path, offset=2, limit=1]'
    assert next(m for m in second_result['messages'] if isinstance(m, ToolMessage)).content == 'a\nb\nc\n'


@async_test
async def test_approval_resume_keeps_the_original_startup_settings(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import create_session_read_tool
    from naiyue_test_agent.settings import load_settings
    from test_application_run_events import make_app, ToolModel
    from test_async_application import call
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    document = tmp_path / 'outside.md'
    document.write_text('a\nb\nc\n')
    path = tmp_path / 'settings.toml'
    path.write_text('[tools.read]\ndefault_limit=1\n')
    reader = create_session_read_tool(settings=load_settings(path).tools.read)
    app = await make_app(monkeypatch,
        ToolModel(responses=[call('read', {'path': str(document)}), AIMessage(content='done')]),
        [reader])
    session = await app.acreate_workspace_session('A', workspace)
    pending = await app.arun(session, [('user', 'read')])
    assert len(pending['approval_requests']) == 1
    assert not any(isinstance(m, ToolMessage) for m in pending['messages'])
    path.write_text('[tools.read]\ndefault_limit=3\n')
    result = await app.aresume_tool_approval(session, ['approve'])
    assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).content == 'a\n[read: lines 1-1; next_offset=2]\n[read: stopped by 1 line page limit; continue with the same path, offset=2, limit=1]'


def test_small_read_page_does_not_remove_skills_from_discovery(tmp_path):
    from naiyue_test_agent.application.skills import discover_project_skills
    from naiyue_test_agent.tools import ReadSettings, create_session_read_tool
    from skill_helpers import write_skill
    path = write_skill(tmp_path, body='body\n' * 100)
    create_session_read_tool(settings=ReadSettings(max_output_bytes=8))
    catalog = discover_project_skills(tmp_path)
    assert len(catalog.entries) == 1
    assert catalog.entries[0].skill_file == path.resolve()


@pytest.mark.parametrize('entry', ['cli', 'web'])
@async_test
async def test_entries_wire_ls_write_and_edit_settings(monkeypatch, tmp_path, entry):
    from naiyue_test_agent.tools import settings as module
    from test_write_tool import RecordingFake
    from test_async_application import StreamingToolModel
    root = tmp_path / 'agent-settings'
    root.mkdir()
    (root / 'settings.toml').write_text(
        '[tools.ls]\ndefault_limit=1\n'
        '[tools.write]\nmax_input_bytes=4\n'
        '[tools.edit]\nmax_input_bytes=9\n')
    monkeypatch.setattr(application_settings, 'get_agent_dir', lambda: root)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    directory = workspace / 'sub'
    directory.mkdir()
    (directory / 'a').touch()
    (directory / 'b').touch()
    (workspace / 'doc').write_text('旧', encoding='utf-8')
    replies = [AIMessage(content='', tool_calls=[
        {'name': 'ls', 'args': {'path': 'sub'}, 'id': 'ls'},
        {'name': 'write', 'args': {'path': 'new', 'content': '中文'}, 'id': 'write'},
        {'name': 'edit', 'args': {'path': 'doc', 'edits': [
            {'oldText': '旧', 'newText': '中文'}]}, 'id': 'edit'},
    ]), AIMessage(content='done')]
    if entry == 'cli':
        from naiyue_test_agent import application
        from naiyue_test_agent.application import cli
        from naiyue_test_agent.model_access import ModelCatalog
        model = RecordingFake(responses=replies)
        monkeypatch.setattr(application, 'load_model_catalog',
                            lambda _: ModelCatalog('A', {'A': model}))
        lines = iter(['check files', ':quit'])
        monkeypatch.setattr('builtins.input', lambda *_: next(lines))
        assert cli.main(['--no-session', '--workspace', str(workspace)]) == 0
    else:
        from test_web_api import web_client, request
        model = StreamingToolModel(replies=replies)
        async def scenario():
            async with web_client(monkeypatch, tmp_path, model, workspace_root=workspace) as (client, app):
                sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
                run = (await client.post(f'/api/sessions/{sid}/runs', json=request('check files'))).json()['run_id']
                await client.get(f'/api/runs/{run}/events')
        await scenario()
    feedback = {m.tool_call_id: m for m in model.seen[1] if isinstance(m, ToolMessage)}
    assert feedback['ls'].status == 'success'
    assert feedback['ls'].content == 'a\n[ls: truncated at 1 entries; narrow the directory or limit]'
    assert feedback['write'].status == 'error' and 'size limit' in feedback['write'].content
    assert feedback['edit'].status == 'success'
    assert not (workspace / 'new').exists()
    assert (workspace / 'doc').read_text() == '中文'


@async_test
async def test_two_applications_keep_their_own_ls_defaults(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import LsSettings, create_session_ls_tool
    from test_application_run_events import make_app, ToolModel
    from test_async_application import call
    (tmp_path / 'a').touch()
    (tmp_path / 'b').touch()
    applications = [await make_app(monkeypatch,
        ToolModel(responses=[call('ls', {}), AIMessage(content='done')]),
        [create_session_ls_tool(settings=LsSettings(default_limit=limit))]) for limit in (1, 2)]
    outputs = []
    for app in applications:
        result = await app.arun(await app.acreate_workspace_session('A', tmp_path), [('user', 'ls')])
        outputs.append(next(m.content for m in result['messages'] if isinstance(m, ToolMessage)))
    assert outputs == ['a\n[ls: truncated at 1 entries; narrow the directory or limit]', 'a\nb']
