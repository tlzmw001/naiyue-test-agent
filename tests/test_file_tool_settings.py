"""Per-tool limits must affect public schemas and actual file IO together."""

from runtime_context_helpers import ExecutionRoot, RunIdentity, typed_context
from naiyue_test_agent import settings as application_settings

from native_application_helpers import async_test

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from naiyue_test_agent import tools
from naiyue_test_agent.tools import settings as module
from tool_feedback_helpers import invoke_tool_feedback


@pytest.mark.parametrize('name, defaults', [
    ('ls', {'default_limit': 500, 'max_limit': 2000,
            'max_output_bytes': 51200, 'max_path_chars': 4096}),
    ('write', {'max_input_bytes': 1048576, 'max_path_chars': 4096, 'max_diff_input_bytes': 1048576,
               'max_diff_bytes': 65536, 'max_preview_bytes': 65536, 'max_patch_bytes': 1048576}),
    ('edit', {'max_input_bytes': 1048576, 'max_path_chars': 4096,
              'max_normalization_buffer_bytes': 1048576, 'max_affected_line_bytes': 1048576,
              'max_diff_bytes': 65536, 'max_preview_bytes': 65536, 'max_patch_bytes': 1048576}),
])
def test_file_settings_defaults_are_frozen(name, defaults):
    setting = getattr(application_settings.AppSettings().tools, name)
    assert setting.model_dump() == defaults
    with pytest.raises(ValidationError, match='frozen'):
        setting.max_path_chars = 10


@pytest.mark.parametrize('name', ['LsSettings', 'WriteSettings', 'EditSettings'])
@pytest.mark.parametrize('value', [0, -1, True, '4', 1.5])
def test_file_settings_reject_invalid_limits(name, value):
    cls = getattr(module, name)
    field = 'max_output_bytes' if name == 'LsSettings' else 'max_input_bytes'
    with pytest.raises(ValidationError):
        cls(**{field: value})


def test_file_settings_load_together_and_invalid_group_falls_back(tmp_path, caplog):
    path = tmp_path / 'settings.toml'
    path.write_text('[tools.ls]\ndefault_limit=2\nmax_limit=5\n'
                    'max_output_bytes=128\nmax_path_chars=20\n'
                    '[tools.write]\nmax_input_bytes=4\n'
                    '[tools.edit]\nmax_input_bytes=12\n')
    settings = application_settings.load_settings(path)
    assert settings.tools.ls.default_limit == 2
    assert settings.tools.ls.max_limit == 5
    assert settings.tools.ls.max_output_bytes == 128
    assert settings.tools.ls.max_path_chars == 20
    assert settings.tools.write.max_input_bytes == 4
    assert settings.tools.edit.max_input_bytes == 12
    path.write_text('[tools.ls]\ndefault_limit=6\nmax_limit=5\n'
                    '[tools.write]\nmax_input_bytes=4\n'
                    'PRIVATE_CONFIG_VALUE="PRIVATE_CONTENT"\n')
    assert application_settings.load_settings(path) == application_settings.AppSettings()
    assert 'tool_settings_fallback reason=invalid' in caplog.text
    assert 'PRIVATE_CONFIG_VALUE' not in caplog.text and 'PRIVATE_CONTENT' not in caplog.text
    with pytest.raises(ValidationError, match='default_limit'):
        module.LsSettings(default_limit=6, max_limit=5)


@async_test
async def test_ls_config_controls_default_explicit_limit_and_feedback(tmp_path):
    for name in ('a', 'b', 'c', 'd'):
        (tmp_path / name).touch()
    tool = tools.create_session_ls_tool(settings=tools.LsSettings(
        default_limit=2, max_limit=3, max_output_bytes=128))
    default = (await invoke_tool_feedback(tool, {}, call_id='default', context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None))))
    larger = (await invoke_tool_feedback(tool, {'limit': 3}, call_id='larger', context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None))))
    invalid = (await invoke_tool_feedback(tool, {'limit': 4}, call_id='invalid', context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None))))
    assert default.content == 'a\nb\n[ls: truncated at 2 entries; narrow the directory or limit]'
    assert larger.content == 'a\nb\nc\n[ls: truncated at 3 entries; narrow the directory or limit]'
    assert invalid.status == 'error' and '1 to 3' in invalid.content
    schema = tool.tool.tool_call_schema.model_json_schema()['properties']
    assert schema['limit']['default'] == 2 and schema['limit']['maximum'] == 3
    assert '128 bytes' in tool.tool.description and '2 entries' in tool.tool.description


@async_test
async def test_ls_output_budget_counts_utf8_and_reports_its_actual_limit(tmp_path):
    (tmp_path / '中').touch()
    (tmp_path / '文').touch()
    tool = tools.create_session_ls_tool(settings=tools.LsSettings(max_output_bytes=6))
    result = (await invoke_tool_feedback(tool, {}, call_id='ls', context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None))))
    assert result.status == 'success'
    assert result.content == '中\n[ls: truncated at 6 bytes output; narrow the directory or limit]'


@pytest.mark.parametrize('name, args', [
    ('ls', {'path': '.'}), ('write', {'path': 'x', 'content': 'ok'}),
    ('edit', {'path': 'x', 'edits': [{'oldText': 'a', 'newText': 'b'}]}),
])
def test_session_tool_schemas_hide_runtime_and_enforce_configured_paths(name, args):
    settings_cls = getattr(tools, name.title() + 'Settings')
    tool = getattr(tools, 'create_session_' + name + '_tool')(
        settings=settings_cls(max_path_chars=3))
    assert 'runtime' not in tool.tool.tool_call_schema.model_fields
    # LangChain rebuilds tool_call_schema for model export; execution validates
    # args_schema, whose public base retains strictness and the original validators.
    schema = tool.input_schema
    assert 'runtime' not in schema.model_fields
    assert 'runtime' in tool.tool.args_schema.model_fields
    schema.model_validate(args)
    for bad in ({**args, 'path': 'long'}, {**args, 'path': 1},
                {**args, 'unknown': 'PRIVATE'}, {**args, 'runtime': {}}):
        with pytest.raises(ValidationError):
            schema.model_validate(bad)
    if name in ('write', 'edit'):
        assert tool.execution_mode == 'sequential'
    if name == 'ls':
        with pytest.raises(ValidationError):
            schema.model_validate({'limit': None})
    if name == 'edit':
        with pytest.raises(ValidationError):
            schema.model_validate({'path': 'x', 'edits': [{'oldText': 1, 'newText': 'b'}]})


@pytest.mark.parametrize('name, args, io_name', [
    ('ls', {'path': '.'}, '_list_directory'),
    ('write', {'path': 'x', 'content': 'ok'}, '_write'),
    ('edit', {'path': 'x', 'edits': [{'oldText': 'a', 'newText': 'b'}]}, '_edit'),
])
@async_test
async def test_invalid_configured_arguments_stop_before_io(monkeypatch, tmp_path, name, args, io_name):
    import importlib
    from langchain_core.messages import AIMessage, ToolMessage
    from test_application_run_events import make_app, ToolModel
    entered = []
    monkeypatch.setattr(importlib.import_module('naiyue_test_agent.tools.' + name),
                        io_name, lambda *a, **k: entered.append(True))
    tool = getattr(tools, 'create_session_' + name + '_tool')(
        settings=getattr(tools, name.title() + 'Settings')(max_path_chars=3))
    invalid_args = [{**args, 'path': 'long'}, {**args, 'path': 1},
                    {**args, 'unknown': 'PRIVATE'}]
    # Session Ls includes HITL, so use the existing app helper with a checkpointer.
    app = await make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': name, 'args': invalid, 'id': str(index)}
        for index, invalid in enumerate(invalid_args)]), AIMessage(content='done')]), [tool])
    result = await app.runs.start(await app.sessions.create_workspace('A', tmp_path), [('user', 'run')])
    feedback = [message for message in result['messages'] if isinstance(message, ToolMessage)]
    assert len(feedback) == 3
    assert all(message.status == 'error' for message in feedback)
    assert all('runtime' not in message.content and 'PRIVATE' not in message.content for message in feedback)
    assert entered == []


@async_test
async def test_write_and_edit_keep_independent_utf8_input_budgets(tmp_path):
    target = tmp_path / 'doc'
    target.write_text('旧', encoding='utf-8')
    writer = tools.create_session_write_tool(settings=tools.WriteSettings(max_input_bytes=4))
    editor = tools.create_session_edit_tool(settings=tools.EditSettings(max_input_bytes=9))
    context = typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None))
    failed = (await invoke_tool_feedback(writer, {'path': 'doc', 'content': '中文'},
                                  call_id='write', context=context))
    assert failed.status == 'error' and 'size limit' in failed.content
    assert target.read_text() == '旧'
    edited = (await invoke_tool_feedback(editor, {'path': 'doc', 'edits': [
        {'oldText': '旧', 'newText': '中文'}]}, call_id='edit', context=context))
    assert edited.status == 'success' and target.read_text() == '中文'
    failed = (await invoke_tool_feedback(editor, {'path': 'doc', 'edits': [
        {'oldText': '中文', 'newText': '新内容'}]}, call_id='large', context=context))
    assert failed.status == 'error' and 'input limit' in failed.content
    assert target.read_text() == '中文'
    assert [path.name for path in tmp_path.iterdir()] == ['doc']
    assert '4 bytes' in writer.tool.description and '9 bytes' in editor.tool.description


@async_test
async def test_edit_input_budget_does_not_limit_original_file_size(tmp_path):
    original = 'x' * (128 * 1024) + 'a'
    (tmp_path / 'large').write_text(original)
    editor = tools.create_session_edit_tool(settings=tools.EditSettings(max_input_bytes=2))
    result = (await invoke_tool_feedback(editor, {'path': 'large', 'edits': [
        {'oldText': 'a', 'newText': 'b'}]}, call_id='edit', context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None))))
    assert result.status == 'success'
    assert (tmp_path / 'large').read_text() == original[:-1] + 'b'


def test_old_write_environment_no_longer_changes_tool_construction(monkeypatch):
    monkeypatch.setenv('NAIYUE_WRITE_MAX_BYTES', 'INVALID_OLD_SETTING')
    assert '1048576 bytes' in tools.create_session_write_tool().tool.description
    assert '1048576 bytes' in tools.create_session_edit_tool().tool.description


def test_shared_schema_builder_preserves_base_validators_and_strictness():
    from naiyue_test_agent.tools.schemas import build_tool_schemas
    class Input(BaseModel):
        model_config = ConfigDict(strict=True, extra='forbid')
        value: int
        @field_validator('value')
        @classmethod
        def positive(cls, value):
            if value <= 0:
                raise ValueError('positive value required')
            return value
    public, execution = build_tool_schemas('ExampleInput', Input)
    assert public.model_validate({'value': 1}).value == 1
    assert issubclass(execution, public)
    for args in ({'value': -1}, {'value': '1'}, {'value': 1, 'unknown': 2}):
        with pytest.raises(ValidationError):
            public.model_validate(args)
