"""Declarative roles reuse registration at real memory, SQLite, CLI and Web entries."""

from dataclasses import FrozenInstanceError
from pathlib import Path
import traceback

import pytest
from pydantic import ValidationError

from native_application_helpers import async_test, build_application
from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog, ModelConfigError
from naiyue_test_agent.tools import SubAgentSpec, ToolSettings, load_tool_settings
from test_async_application import StreamingToolModel
from test_dynamic_tools import definition


def test_current_sample_defaults_and_commented_role_are_loadable(tmp_path):
    sample = Path(__file__).resolve().parents[1] / 'docs/examples/settings.example.toml'
    defaults = load_tool_settings(sample)
    assert defaults.subagents == ()
    assert defaults.tools.subagent.max_result_lines == 2000
    assert defaults.tools.subagent.max_result_bytes == 65_536
    text = sample.read_text(encoding='utf-8')
    block = text[text.index('# [[subagents]]'):text.index('# 默认省略model_id/max_model_calls')]
    enabled = '\n'.join(line[2:] if line.startswith('# ') else line for line in block.splitlines())
    path = tmp_path / 'settings.toml'
    path.write_text(enabled, encoding='utf-8')
    spec, = load_tool_settings(path).subagents
    assert spec.name == 'tester' and spec.tool_names == ('read', 'grep', 'find', 'ls', 'bash')
    assert spec.model_id is None and spec.max_model_calls is None
    assert '最终返回检查范围' in spec.system_prompt


CONFIG = '''[[subagents]]
name = "tester"
description = "Run relevant tests."
system_prompt = "PRIVATE_CONFIG_PROMPT"
tool_names = ["probe"]
'''


def test_settings_parse_frozen_definitions_and_keep_missing_roles_empty(tmp_path):
    assert load_tool_settings(tmp_path / 'missing.toml').subagents == ()
    path = tmp_path / 'settings.toml'
    path.write_text(CONFIG + '\n[[subagents]]\nname="writer"\ndescription="Write tests"\n'
                    'system_prompt="Work with evidence"\ntool_names=[]\nmodel_id="A"\nmax_model_calls=3\n')
    settings = load_tool_settings(path)
    spec, writer = settings.subagents
    assert isinstance(settings.subagents, tuple) and isinstance(spec, SubAgentSpec)
    assert spec.tool_names == ('probe',) and spec.model_id is None and spec.max_model_calls is None
    assert (writer.tool_names, writer.model_id, writer.max_model_calls) == ((), 'A', 3)
    with pytest.raises(FrozenInstanceError):
        spec.description = 'changed'
    with pytest.raises(ValidationError):
        settings.subagents = ()
    assert ToolSettings(subagents=(spec,)).subagents[0] is spec


@pytest.mark.parametrize('content', [
    'subagents="PRIVATE_BAD_VALUE"',
    '[[subagents]]\nsystem_prompt="PRIVATE_CONFIG_PROMPT"\n',
    CONFIG.replace('name = "tester"', 'name = "unsafe/name"'),
    CONFIG.replace('description = "Run relevant tests."', 'description = ""'),
    CONFIG.replace('tool_names = ["probe"]', 'tool_names = "probe"'),
    CONFIG.replace('tool_names = ["probe"]', 'tool_names = ["probe", "probe"]'),
    CONFIG.replace('tool_names = ["probe"]', 'tool_names = ["subagent"]'),
    CONFIG.replace('tool_names = ["probe"]', 'tool_names = [7]'),
    CONFIG + 'max_model_calls=0\n', CONFIG + 'max_model_calls=true\n',
    CONFIG + 'model_id=3\n', CONFIG + 'unknown="PRIVATE_UNKNOWN"\n',
    CONFIG.replace('system_prompt = "PRIVATE_CONFIG_PROMPT"\n', ''),
    CONFIG + '\n' + CONFIG,
])
def test_invalid_role_settings_fail_without_budget_fallback_or_prompt_leak(tmp_path, caplog, content):
    path = tmp_path / 'settings.toml'
    path.write_text(content)
    with pytest.raises(ValueError, match='Subagent settings') as caught:
        load_tool_settings(path)
    assert 'subagent_settings_invalid' in caplog.text
    assert 'PRIVATE' not in caplog.text + str(caught.value)
    assert 'PRIVATE' not in ''.join(traceback.format_exception(caught.value))
    assert 'fallback' not in caplog.text


def test_invalid_tool_budget_keeps_valid_role_definitions(tmp_path, caplog):
    path = tmp_path / 'settings.toml'
    path.write_text(CONFIG + '\n[tools.read]\nmax_limit=0\n')
    settings = load_tool_settings(path)
    assert settings.tools == ToolSettings().tools
    assert settings.subagents[0].name == 'tester'
    assert settings.subagents[0].tool_names == ('probe',)
    assert 'tool_settings_fallback' in caplog.text and 'PRIVATE_CONFIG_PROMPT' not in caplog.text


@async_test
async def test_configured_roles_are_a_startup_snapshot_and_custom_tools_are_referenced(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import settings as module
    monkeypatch.setattr(module, 'get_agent_dir', lambda: tmp_path)
    path = tmp_path / 'settings.toml'
    path.write_text(CONFIG)
    models = []
    def catalog(_):
        model = StreamingToolModel(replies=[])
        models.append(model)
        return ModelCatalog('A', {'A': model})
    monkeypatch.setattr(application, 'load_model_catalog', catalog)
    tools = [definition(), definition('extra')]
    first = await build_application('unused', tools=tools, initial_active_tools=[],
                                    system_prompt='', response_format=None)
    original = first.get_registered_subagents()[0]
    path.write_text(CONFIG.replace('["probe"]', '["probe", "extra"]')
                    .replace('PRIVATE_CONFIG_PROMPT', 'UPDATED_CONFIG_PROMPT'))
    second = await build_application('unused', tools=tools, initial_active_tools=[],
                                     system_prompt='', response_format=None)
    assert first.get_registered_subagents() == (original,)
    assert original.tool_names == ('probe',) and original.system_prompt == 'PRIVATE_CONFIG_PROMPT'
    updated = second.get_registered_subagents()[0]
    assert updated.tool_names == ('probe', 'extra') and updated.system_prompt == 'UPDATED_CONFIG_PROMPT'
    sid = await second.acreate_session('A')
    assert second.get_active_tools(sid) == () and await second.aget_messages(sid) == []
    assert all(model.seen == [] for model in models)


@pytest.mark.parametrize('persistent', [False, True])
@pytest.mark.parametrize('failure', ['tool', 'model'])
@async_test
async def test_invalid_startup_dependencies_raise_and_release_owned_resources(monkeypatch, tmp_path, persistent, failure):
    from naiyue_test_agent.application.async_application import build_async_local_application
    from naiyue_test_agent.application.session_storage import SessionStorage
    closed, catalogs = [], []
    def catalog(_):
        instance = ModelCatalog('A', {'A': StreamingToolModel(replies=[])})
        close = instance.aclose
        async def record_close():
            closed.append(instance)
            await close()
        instance.aclose = record_close
        catalogs.append(instance)
        return instance
    monkeypatch.setattr(application, 'load_model_catalog', catalog)
    spec = SubAgentSpec(name='tester', description='Run tests', system_prompt='PRIVATE',
                        tool_names=('missing',) if failure == 'tool' else (),
                        model_id='missing' if failure == 'model' else None)
    valid = SubAgentSpec('valid', 'Valid', 'Prompt', ())
    options = dict(tools=[], system_prompt='', response_format=None, settings=ToolSettings(subagents=(valid, spec)))
    error_type = ValueError if failure == 'tool' else ModelConfigError
    database = tmp_path / 'roles.sqlite'
    with pytest.raises(error_type):
        if persistent:
            async with open_async_persistent_application('unused', database_path=database, **options):
                pytest.fail('Invalid roles must prevent application publication')
        else:
            await build_async_local_application('unused', **options)
    assert len(catalogs) == 1 and closed == catalogs
    if persistent:
        with SessionStorage(database) as storage:
            assert storage.list_sessions() == []


@async_test
async def test_persistent_app_reregisters_current_config_without_persisting_definitions(monkeypatch, tmp_path):
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': StreamingToolModel(replies=[])}))
    path = tmp_path / 'settings.toml'
    path.write_text(CONFIG)
    options = dict(database_path=tmp_path / 'roles.sqlite', tools=[definition()], system_prompt='', response_format=None)
    async with open_async_persistent_application('unused', settings=load_tool_settings(path), **options) as app:
        assert [spec.name for spec in app.get_registered_subagents()] == ['tester']
        await app.aregister_subagent(SubAgentSpec('temporary', 'Temporary', 'Prompt', ()))
    path.write_text(CONFIG.replace('name = "tester"', 'name = "updated"'))
    async with open_async_persistent_application('unused', settings=load_tool_settings(path), **options) as app:
        assert [spec.name for spec in app.get_registered_subagents()] == ['updated']


@pytest.mark.parametrize('invalid', [False, True])
def test_cli_main_consumes_configured_roles_and_reports_registration_failures(monkeypatch, tmp_path, capsys, invalid):
    from naiyue_test_agent.application import cli
    from naiyue_test_agent.tools import settings as module
    root = tmp_path / 'settings'
    root.mkdir()
    (root / 'settings.toml').write_text(CONFIG.replace('["probe"]', '["missing"]' if invalid else '["read"]'))
    monkeypatch.setattr(module, 'get_agent_dir', lambda: root)
    model = StreamingToolModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    seen = []
    def read(_prompt=''):
        assert model.seen == []
        return ':quit'
    monkeypatch.setattr('builtins.input', read)
    original = cli.build_local_application
    def build(*args, **kwargs):
        app = original(*args, **kwargs)
        seen.extend(app.get_registered_subagents())
        return app
    monkeypatch.setattr(cli, 'build_local_application', build)
    assert cli.main(['--no-session', '--workspace', str(tmp_path)]) == (1 if invalid else 0)
    assert [spec.name for spec in seen] == ([] if invalid else ['tester'])
    output = capsys.readouterr().out
    assert ('初始化失败' in output) is invalid and 'PRIVATE_CONFIG_PROMPT' not in output
    assert model.seen == []


@pytest.mark.parametrize('invalid', [False, True])
@async_test
async def test_web_lifespan_consumes_the_same_role_configuration(monkeypatch, tmp_path, invalid):
    from test_web_api import web_client
    from naiyue_test_agent.tools import settings as module
    root = tmp_path / 'settings'
    root.mkdir()
    (root / 'settings.toml').write_text(CONFIG.replace('["probe"]', '["missing"]' if invalid else '["read"]'))
    monkeypatch.setattr(module, 'get_agent_dir', lambda: root)
    model = StreamingToolModel(replies=[])
    async def scenario():
        async with web_client(monkeypatch, tmp_path, model, workspace_root=tmp_path) as (client, web):
            app = web.state.service.application
            assert [spec.name for spec in app.get_registered_subagents()] == ['tester']
            assert (await client.get('/api/models')).status_code == 200
            assert model.seen == []
    if invalid:
        with pytest.raises(ValueError, match='Unknown tool'):
            await scenario()
    else:
        await scenario()
