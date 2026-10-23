"""Only valid, explicit tool definitions enter an application."""

from naiyue_test_agent.contracts.run_context import RuntimeContext

from native_application_helpers import async_test, build_application

from naiyue_test_agent.tools.permissions import ToolPermission

from dataclasses import FrozenInstanceError

import pytest
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, field_validator


class ProbeInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    value: int = Field(default=1, ge=1, le=3)

    @field_validator('value')
    @classmethod
    def odd(cls, value):
        if value % 2 == 0:
            raise ValueError('odd values only')
        return value


def probe_tool(name='probe', **kwargs):
    @tool(name, args_schema=ProbeInput, **kwargs)
    def probe(value: int = 1) -> str:
        """Return a supplied synthetic value."""
        return str(value)
    return probe


def test_registry_preserves_identity_defaults_and_requested_order():
    from naiyue_test_agent.tools import ToolSpec, ToolRegistry
    registry = ToolRegistry()
    first, second = probe_tool(), probe_tool('other')
    specs = (ToolSpec(first, ProbeInput, permissions=ToolPermission(default='allow')), ToolSpec(second, ProbeInput, permissions=ToolPermission(default='allow')))
    for spec in specs:
        registry.register(spec)
    assert registry.names() == ('probe', 'other')
    assert registry.get('probe') is specs[0]
    assert registry.get('probe').tool is first
    assert registry.select(['other', 'probe']) == (specs[1], specs[0])
    assert registry.select([]) == ()
    assert specs[0].execution_mode == 'parallel' and specs[0].side_effects == 'unknown'
    with pytest.raises(FrozenInstanceError):
        specs[0].execution_mode = 'sequential'


def test_failed_registration_and_selection_keep_original_registry():
    from naiyue_test_agent.tools import ToolSpec, ToolRegistry
    registry = ToolRegistry()
    spec = ToolSpec(probe_tool(), ProbeInput, permissions=ToolPermission(default='allow'))
    registry.register(spec)
    with pytest.raises(ValueError, match='Duplicate tool name'):
        registry.register(ToolSpec(probe_tool(), ProbeInput, permissions=ToolPermission(default='allow')))
    with pytest.raises(ValueError, match='Unknown tool'):
        registry.get('missing')
    with pytest.raises(ValueError, match='Duplicate'):
        registry.select(['probe', 'probe'])
    with pytest.raises(ValueError, match='Unknown tool'):
        registry.select(['probe', 'missing'])
    assert registry.names() == ('probe',) and registry.get('probe') is spec


@pytest.mark.parametrize('changes', [
    {'execution_mode': 'sometimes'}, {'side_effects': 'safe'},
    {'input_schema': dict}, {'tool': object()},
])
def test_invalid_spec_is_rejected_without_partial_registration(changes):
    from naiyue_test_agent.tools import ToolSpec, ToolRegistry
    registry = ToolRegistry()
    kwargs = {'tool': probe_tool(), 'input_schema': ProbeInput, **changes}
    with pytest.raises(ValueError):
        registry.register(ToolSpec(**kwargs, permissions=ToolPermission(default='allow')))
    assert registry.names() == ()


@pytest.mark.parametrize('field, value', [
    ('name', ''), ('name', 'two words'), ('description', ''),
    ('handle_tool_error', True), ('return_direct', True),
    ('response_format', 'unsupported'),
])
def test_unsupported_basetool_contract_fails_registration(field, value):
    from naiyue_test_agent.tools import ToolSpec, ToolRegistry
    original = probe_tool()
    setattr(original, field, value)
    with pytest.raises(ValueError):
        ToolRegistry().register(ToolSpec(original, ProbeInput, permissions=ToolPermission(default='allow')))


def test_public_model_and_execution_model_share_validation_and_hide_runtime():
    from naiyue_test_agent.tools import ToolSpec, ToolRegistry
    from naiyue_test_agent.tools.schemas import build_tool_schemas
    public, execution = build_tool_schemas('PublicProbeInput', ProbeInput)
    @tool(args_schema=execution)
    def probe(runtime: ToolRuntime[RuntimeContext], value: int = 1) -> str:
        """Return a value with trusted runtime context."""
        return str(value)
    spec = ToolSpec(probe, public, permissions=ToolPermission(default='allow'))
    registry = ToolRegistry()
    registry.register(spec)
    assert registry.get('probe').input_schema is public
    assert 'runtime' not in probe.tool_call_schema.model_fields
    assert public.model_validate({}).value == 1
    for args in ({'value': '1'}, {'value': 2}, {'value': 4}, {'unexpected': True}):
        with pytest.raises(ValidationError):
            public.model_validate(args)
    with pytest.raises(ValueError, match='public|input|schema|injected'):
        ToolRegistry().register(ToolSpec(probe, execution, permissions=ToolPermission(default='allow')))


@pytest.mark.parametrize('change', ['unrelated', 'field', 'config', 'validator'])
def test_divergent_execution_model_is_rejected(change):
    from naiyue_test_agent.tools import ToolSpec, ToolRegistry
    if change == 'unrelated':
        execution = create_model('Unrelated', value=(int, 1))
    elif change == 'field':
        execution = create_model('ChangedField', __base__=ProbeInput, value=(str, '1'))
    elif change == 'config':
        execution = create_model('ChangedConfig', __base__=ProbeInput,
                                 __config__=ConfigDict(strict=False))
    else:
        class ChangedValidator(ProbeInput):
            @field_validator('value')
            @classmethod
            def positive(cls, value):
                return value
        execution = ChangedValidator
    @tool(args_schema=execution)
    def probe(value=1) -> str:
        """A definition with diverging public and execution validation."""
        return str(value)
    with pytest.raises(ValueError, match='input|schema|validation'):
        ToolRegistry().register(ToolSpec(probe, ProbeInput, permissions=ToolPermission(default='allow')))


@pytest.mark.parametrize('entry', ['local', 'persistent'])
@async_test
async def test_invalid_initial_registry_precedes_model_and_storage_acquisition(monkeypatch, tmp_path, entry):
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_application import build_async_local_application
    from naiyue_test_agent.application.async_persistent import open_async_persistent_application
    from naiyue_test_agent.application import async_persistent
    entered = []
    def unexpected(*args, **kwargs):
        entered.append(True)
        raise AssertionError('invalid registration must fail before resource acquisition')
    monkeypatch.setattr(application, 'load_model_catalog', unexpected)
    monkeypatch.setattr(async_persistent, 'ProjectStore', unexpected)
    options = dict(tools=[probe_tool()], system_prompt='', response_format=None)
    with pytest.raises(ValueError, match='ToolSpec'):
        if entry == 'local':
            await build_application('synthetic', **options)
        else:
            async with open_async_persistent_application('synthetic', database_path=tmp_path / 'sessions.sqlite', **options):
                pytest.fail('Invalid configuration must not acquire an application')
    assert entered == [] and not (tmp_path / 'sessions.sqlite').exists()


def test_builtin_collection_has_real_schemas_settings_and_capability_declarations():
    from naiyue_test_agent.tools import create_builtin_tools
    from naiyue_test_agent.settings import AppSettings
    settings = AppSettings.model_validate({'tools': {'ls': {'default_limit': 2}}})
    specs = create_builtin_tools(settings.tools, process_settings=settings.process, shell_task_settings=settings.shell_tasks, progress_settings=settings.progress)
    assert [spec.tool.name for spec in specs] == ['read', 'ls', 'find', 'grep', 'write', 'edit', 'bash', 'powershell', 'subagent', 'subagent_list', 'aitest_kit', 'web_search', 'web_fetch', 'task_list', 'task_output', 'task_stop', 'history', 'memory']
    assert [spec.execution_mode for spec in specs] == ['parallel'] * 4 + ['sequential'] * 4 + ['parallel', 'parallel', 'sequential', 'parallel', 'parallel', 'parallel', 'parallel', 'sequential', 'parallel', 'parallel']
    assert [spec.side_effects for spec in specs] == ['read_only'] * 4 + ['may_mutate'] * 4 + ['unknown', 'read_only', 'may_mutate', 'read_only', 'read_only', 'read_only', 'read_only', 'may_mutate', 'read_only', 'may_mutate']
    assert specs[1].input_schema.model_fields['limit'].default == 2
    assert all('runtime' not in spec.input_schema.model_fields for spec in specs)
    assert all(issubclass(spec.tool.args_schema, spec.input_schema) for spec in specs)
    bash = next(s for s in specs if s.tool.name == 'bash')
    assert bash.tool.name == 'bash' and bash.execution_mode == 'sequential'
    assert bash.side_effects == 'may_mutate'


@pytest.mark.parametrize('native_model', [False, True])
@async_test
async def test_registered_artifact_uses_original_tool_and_survives_checkpoint(monkeypatch, tmp_path, native_model):
    from naiyue_test_agent.tools import ToolSpec
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_persistent import open_async_persistent_application
    from naiyue_test_agent.application.async_persistent import open_async_persistent_application
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.model_access import ModelCatalog
    from test_write_tool import RecordingFake
    from test_async_application import StreamingToolModel
    @tool(args_schema=ProbeInput, response_format='content_and_artifact')
    def probe(value: int = 1) -> tuple[str, dict]:
        """Return a public summary with a separate JSON detail."""
        return str(value), {'detail': 'PRIVATE_ARTIFACT', 'value': value}
    spec = ToolSpec(probe, ProbeInput, side_effects='read_only', permissions=ToolPermission(default='allow'))
    replies = [AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {}, 'id': 'artifact'}]), AIMessage(content='done')]
    model = StreamingToolModel(replies=replies) if native_model else RecordingFake(responses=replies)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    async def scenario():
        async with open_async_persistent_application('synthetic.json', database_path=tmp_path / 'async.sqlite',
            tools=[spec], system_prompt='', response_format=None) as app:
            assert app.get_registered_tools() == (spec,)
            assert app.get_registered_tools()[0] is spec
            assert app.get_registered_tools()[0].tool is probe
            sid = await app.acreate_session('A')
            await app.arun(sid, [('user', 'run')])
            return await app.aget_messages(sid)
    messages = await scenario()
    feedback = next(message for message in messages if isinstance(message, ToolMessage))
    assert (feedback.tool_call_id, feedback.content, feedback.artifact) == (
        'artifact', '1', {'detail': 'PRIVATE_ARTIFACT', 'value': 1})
    assert 'PRIVATE_ARTIFACT' not in feedback.content
    assert model.seen[1][-1].content == '1'
