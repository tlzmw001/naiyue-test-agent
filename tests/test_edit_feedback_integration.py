"""Edit artifact persistence and failure boundaries, with real temporary files."""

from native_application_helpers import async_test, build_application

from dataclasses import asdict
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_openai.chat_models.base import _convert_message_to_dict

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.context_compaction import _serialize_text_history
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_session_edit_tool
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import edit as module
from naiyue_test_agent.tools import edit_matching
from naiyue_test_agent.web.serialization import message_view
from test_async_application import StreamingToolModel
from test_edit_tool import RecordingFake
from tool_feedback_helpers import invoke_tool_feedback


def tool_call(path='text.txt'):
    return {'name': 'edit', 'id': 'edit-json', 'args': {'path': path,
        'edits': [{'oldText': '"old"', 'newText': '"NEW"'}]}}


@pytest.mark.parametrize('native_model', [False, True])
@async_test
async def test_edit_json_artifact_survives_sqlite_reopen_but_is_not_model_payload(monkeypatch, tmp_path, native_model):
    (tmp_path / 'text.txt').write_text('“old”\n')
    replies = [AIMessage(content='', tool_calls=[tool_call()]), AIMessage(content='done')]
    model = StreamingToolModel(replies=replies) if native_model else RecordingFake(responses=replies)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    tools, events = [create_session_edit_tool()], []
    options = dict(tools=tools, settings=AppSettings(), system_prompt='', response_format=None)
    database = tmp_path / 'sessions.sqlite'
    async def scenario():
        async with open_async_persistent_application('synthetic', database_path=database, **options) as app:
            app.subscribe_events(events.append)
            sid = await app.acreate_workspace_session('A', tmp_path)
            await app.arun(sid, [('user', 'edit')])
            before = await app.aget_messages(sid)
        async with open_async_persistent_application('synthetic', database_path=database, **options) as app:
            await app.arestore_session(sid)
            return before, await app.aget_messages(sid)
    before, after = await scenario()
    first = next(m for m in before if isinstance(m, ToolMessage))
    restored = next(m for m in after if isinstance(m, ToolMessage))
    assert first.status == restored.status == 'success'
    assert first.tool_call_id == restored.tool_call_id == 'edit-json'
    assert first.artifact == restored.artifact == json.loads(json.dumps(first.artifact))
    assert first.artifact['patch'] and first.artifact['firstChangedLine'] == 1
    assert (tmp_path / 'text.txt').read_bytes() == b'"NEW"\n'
    # Canonical ToolMessage may retain artifact; the official wire adapter sends only content.
    wire = _convert_message_to_dict(restored)
    assert wire['content'] == restored.content and 'artifact' not in wire
    without = restored.model_copy(update={'artifact': None})
    assert count_tokens_approximately([restored]) == count_tokens_approximately([without])
    assert _serialize_text_history([restored]) == _serialize_text_history([without])
    assert 'artifact' not in message_view(restored)
    assert 'patch_omitted' not in json.dumps([asdict(event) for event in events])
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == 'edit-json' for m in model.seen[1])


@async_test
async def test_feedback_failure_is_before_commit_and_cleans_temp(monkeypatch, tmp_path, caplog):
    target = tmp_path / 'text.txt'
    target.write_text('“old”\n')
    def fail_feedback(*args):
        assert target.read_text() == '“old”\n'
        raise RuntimeError('PRIVATE_FEEDBACK_DETAIL')
    monkeypatch.setattr(module, 'build_edit_feedback', fail_feedback)
    result = (await invoke_tool_feedback(create_session_edit_tool(), tool_call()['args'],
        call_id='feedback-failed', context={'workspace_root': tmp_path}))
    assert result.status == 'error' and 'I/O' in result.content
    assert target.read_text() == '“old”\n'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['text.txt']
    assert 'phase=prepare error_type=RuntimeError' in caplog.text
    assert 'PRIVATE_FEEDBACK_DETAIL' not in caplog.text


@async_test
async def test_swap_logical_symlink_after_feedback_never_commits_old_or_new_target(monkeypatch, tmp_path):
    target, other, link = tmp_path / 'real.txt', tmp_path / 'other.txt', tmp_path / 'link.txt'
    target.write_text('“old”\n'); other.write_text('other\n'); link.symlink_to(target)
    real_feedback = module.build_edit_feedback
    def swap(*args):
        result = real_feedback(*args)
        link.unlink()
        link.symlink_to(other)
        return result
    monkeypatch.setattr(module, 'build_edit_feedback', swap)
    result = (await invoke_tool_feedback(create_session_edit_tool(), tool_call(link.name)['args'],
        call_id='swap', context={'workspace_root': tmp_path}))
    assert result.status == 'error' and 'changed' in result.content
    assert target.read_text() == '“old”\n' and other.read_text() == 'other\n'
    assert link.is_symlink() and link.resolve() == other
    assert not list(tmp_path.glob('.naiyue-write-*'))


@async_test
async def test_file_change_after_normalized_scan_is_not_overwritten(monkeypatch, tmp_path):
    target = tmp_path / 'text.txt'
    target.write_text('“old”\n')
    real_scan = edit_matching.scan_normalized_matches
    def change(*args, **kwargs):
        result = real_scan(*args, **kwargs)
        target.write_text('another writer\n')
        return result
    monkeypatch.setattr(edit_matching, 'scan_normalized_matches', change)
    result = (await invoke_tool_feedback(create_session_edit_tool(), tool_call()['args'],
        call_id='changed', context={'workspace_root': tmp_path}))
    assert result.status == 'error' and 'changed' in result.content
    assert target.read_text() == 'another writer\n'
    assert not list(tmp_path.glob('.naiyue-write-*'))


@async_test
async def test_approval_does_not_read_original_for_preview(monkeypatch, tmp_path):
    root, outside = tmp_path / 'workspace', tmp_path / 'outside.txt'
    root.mkdir(); outside.write_text('“old”\n')
    model = RecordingFake(responses=[AIMessage(content='', tool_calls=[tool_call(str(outside))]), AIMessage(content='done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = await build_application('synthetic', tools=[create_session_edit_tool()],
        settings=AppSettings(), system_prompt='', response_format=None)
    sid = await app.acreate_workspace_session('A', root)
    calls, real_scan = [], module._scan_target
    def scan(*args, **kwargs):
        calls.append(True)
        return real_scan(*args, **kwargs)
    monkeypatch.setattr(module, '_scan_target', scan)
    await app.arun(sid, [('user', 'edit outside')])
    assert calls == [] and outside.read_text() == '“old”\n'
    assert app.pending_tool_approvals(sid)
    result = await app.aresume_tool_approval(sid, ['approve'])
    feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert calls == [True] and feedback.status == 'success'
    assert outside.read_bytes() == b'"NEW"\n'
