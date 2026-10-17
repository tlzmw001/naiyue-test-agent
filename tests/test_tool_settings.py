"""Configured Read uses one validated budget for schema, feedback, and IO."""

from native_application_helpers import async_test

from pathlib import Path

import pytest
from pydantic import ValidationError

from tool_feedback_helpers import invoke_tool_feedback as _invoke_tool_feedback


async def invoke_tool_feedback(reader, args, root):
    return (await _invoke_tool_feedback(reader, args, call_id='settings-read', context={'workspace_root': root}))


def test_missing_settings_use_defaults_without_creating_a_file(tmp_path, caplog):
    from naiyue_test_agent.tools.settings import ToolSettings, load_tool_settings
    path = tmp_path / 'absent' / 'settings.toml'
    settings = load_tool_settings(path)
    assert settings == ToolSettings()
    assert not path.parent.exists()
    assert 'tool_settings_fallback' not in caplog.text


def test_default_directory_is_shared_with_model_configuration(monkeypatch, tmp_path):
    from naiyue_test_agent.config_paths import get_agent_dir, get_models_path
    from naiyue_test_agent.tools import settings as module
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(module, 'get_agent_dir', get_agent_dir)
    root = tmp_path / '.naiyue-test-agent'
    root.mkdir()
    (root / 'settings.toml').write_text('[tools.read]\ndefault_limit=2\n')
    assert get_models_path() == root / 'models.json'
    assert module.load_tool_settings().tools.read.default_limit == 2


def test_valid_settings_fill_missing_fields_and_are_frozen(tmp_path):
    from naiyue_test_agent.tools.settings import load_tool_settings
    path = tmp_path / 'settings.toml'
    path.write_text('[tools.read]\ndefault_limit=2\nmax_limit=5\n')
    settings = load_tool_settings(path)
    assert settings.tools.read.default_limit == 2
    assert settings.tools.read.max_limit == 5
    assert settings.tools.read.max_output_bytes == 65536
    with pytest.raises(ValidationError):
        settings.tools.read.max_limit = 10
    with pytest.raises(ValidationError):
        settings.tools.read = settings.tools.read


@pytest.mark.parametrize('content', [
    '[tools.read]\ndefault_limit=8\nmax_limit=3\n',
    '[tools.read]\nmax_limit=0\n',
    '[tools.read]\nmax_output_bytes=-1\n',
    '[tools.read]\nmax_path_chars=true\n',
    '[tools.read]\nmax_limit="PRIVATE_NUMBER"\n',
    '[tools.read]\nunknown="PRIVATE_VALUE"\n',
])
def test_invalid_settings_warn_and_fall_back_as_a_whole(tmp_path, caplog, content):
    from naiyue_test_agent.tools.settings import ToolSettings, load_tool_settings
    path = tmp_path / 'settings.toml'
    path.write_text(content)
    assert load_tool_settings(path) == ToolSettings()
    assert sum(r.message.startswith('tool_settings_fallback') for r in caplog.records) == 1
    assert 'PRIVATE' not in caplog.text


@pytest.mark.parametrize('failure', [PermissionError('PRIVATE_PATH'), UnicodeDecodeError('utf8', b'\xff', 0, 1, 'PRIVATE')])
def test_unreadable_settings_warn_without_disclosing_raw_errors(monkeypatch, tmp_path, caplog, failure):
    from naiyue_test_agent.tools.settings import ToolSettings, load_tool_settings
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(Path, 'open', fail)
    with pytest.raises(ValueError, match='Permission settings could not be read or parsed'):
        load_tool_settings(tmp_path / 'settings.toml')
    assert 'permission_settings_unavailable' in caplog.text
    assert 'PRIVATE' not in caplog.text


def test_programming_failure_is_not_converted_to_default_settings(monkeypatch, tmp_path):
    from naiyue_test_agent.tools.settings import load_tool_settings
    error = RuntimeError('programming failure')
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(Path, 'open', fail)
    with pytest.raises(RuntimeError) as caught:
        load_tool_settings(tmp_path / 'settings.toml')
    assert caught.value is error


@async_test
async def test_configured_schema_feedback_and_read_page_agree(tmp_path):
    from naiyue_test_agent.tools import ReadSettings, create_session_read_tool
    (tmp_path / 'doc.md').write_text('a\nb\nc\nd\n')
    reader = create_session_read_tool(settings=ReadSettings(default_limit=2, max_limit=3, max_output_bytes=64))
    props = reader.tool.tool_call_schema.model_json_schema()['properties']
    assert props['limit']['anyOf'][0]['maximum'] == 3
    assert 'runtime' not in props
    assert '2 lines' in reader.tool.description and '64 bytes' in reader.tool.description
    assert (await invoke_tool_feedback(reader, {'path': 'doc.md'}, tmp_path)).content == 'a\nb\n[read: lines 1-2; next_offset=3]\n[read: stopped by 2 line page limit; continue with the same path, offset=3, limit=2]'
    assert (await invoke_tool_feedback(reader, {'path': 'doc.md', 'limit': 3}, tmp_path)).content == 'a\nb\nc\n[read: lines 1-3; next_offset=4]\n[read: stopped by 3 line page limit; continue with the same path, offset=4, limit=3]'
    invalid = (await invoke_tool_feedback(reader, {'path': 'doc.md', 'limit': 4}, tmp_path))
    assert invalid.status == 'error' and '1 to 3' in invalid.content


@async_test
async def test_configured_utf8_budget_and_long_line_feedback(tmp_path):
    from naiyue_test_agent.tools import ReadSettings, create_session_read_tool
    reader = create_session_read_tool(settings=ReadSettings(max_output_bytes=4))
    (tmp_path / 'doc.md').write_text('中\n文\n', encoding='utf-8')
    result = (await invoke_tool_feedback(reader, {'path': 'doc.md'}, tmp_path))
    assert result.content == '中\n[read: lines 1-1; next_offset=2]\n[read: stopped by 4 byte page limit; continue with the same path, offset=2, limit=2000]'
    (tmp_path / 'long.md').write_text('abcde')
    partial = (await invoke_tool_feedback(reader, {'path': 'long.md'}, tmp_path))
    assert partial.status == 'success'
    assert partial.content == ('abcd\n[read: line 1 partial; returned_bytes=4; byte_limit=4; '
        'next_offset=unavailable; line remainder omitted. offset/limit cannot resume within this line; '
        'use a shell to inspect the remainder.]')


@async_test
async def test_strict_injected_schema_and_path_limits_reject_before_io(tmp_path, monkeypatch):
    from naiyue_test_agent.tools import ReadSettings, create_session_read_tool
    from naiyue_test_agent.tools import read as module
    from test_async_application import call
    from test_application_run_events import make_app, ToolModel
    from langchain_core.messages import AIMessage, ToolMessage
    settings = ReadSettings(max_limit=3, max_path_chars=4, default_limit=2)
    reader = create_session_read_tool(settings=settings)
    schema = reader.tool.tool_call_schema.model_json_schema()
    assert 'runtime' not in schema['properties']
    assert schema['properties']['path']['maxLength'] == 4
    entered = []
    monkeypatch.setattr(module, '_read', lambda *a, **k: entered.append(True))
    model = ToolModel(responses=[
        call('read', {'path': 'doc', 'limit': '2'}),
        call('read', {'path': 'long-name'}),
        AIMessage(content='done'),
    ])
    app = await make_app(monkeypatch, model, [reader])
    result = await app.arun(await app.acreate_workspace_session('A', tmp_path), [('user', 'read')])
    feedback = [m for m in result['messages'] if isinstance(m, ToolMessage)]
    assert len(feedback) == 2 and all(m.status == 'error' for m in feedback)
    assert all('runtime' not in m.content and 'workspace_root' not in m.content for m in feedback)
    assert entered == []


@async_test
async def test_two_tools_keep_independent_settings_and_small_file_behavior(tmp_path):
    from naiyue_test_agent.tools import ReadSettings, create_session_read_tool
    (tmp_path / 'doc.md').write_text('a\nb\nc\n')
    first = create_session_read_tool(settings=ReadSettings(default_limit=1))
    second = create_session_read_tool(settings=ReadSettings(default_limit=3))
    assert (await invoke_tool_feedback(first, {'path': 'doc.md'}, tmp_path)).content == 'a\n[read: lines 1-1; next_offset=2]\n[read: stopped by 1 line page limit; continue with the same path, offset=2, limit=1]'
    assert (await invoke_tool_feedback(second, {'path': 'doc.md'}, tmp_path)).content == 'a\nb\nc\n'
    assert (await invoke_tool_feedback(first, {'path': 'doc.md'}, tmp_path)).content == 'a\n[read: lines 1-1; next_offset=2]\n[read: stopped by 1 line page limit; continue with the same path, offset=2, limit=1]'
