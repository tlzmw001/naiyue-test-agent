"""The assembled graph must keep canonical pixels separate from request carriers."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.application.persistent import build_persistent_application
from naiyue_test_agent.application.session_storage import SessionStorage
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.model_access.messages import MODEL_INPUT_METADATA_KEY
from naiyue_test_agent.tools import ToolSettings, create_session_read_tool
from test_async_application import StreamingToolModel
from test_edit_tool import RecordingFake
from test_read_images import png_bytes


def read_call(path='image.png'):
    return AIMessage(content='', tool_calls=[{'name': 'read', 'id': 'image', 'args': {'path': path}}])


@pytest.mark.parametrize('async_mode', [False, True])
@pytest.mark.parametrize('api', ['chat_completions', 'responses'])
def test_image_runtime_and_sqlite_reopen_keep_pixels_without_carriers(monkeypatch, tmp_path, async_mode, api):
    (tmp_path / 'image.png').write_bytes(png_bytes())
    replies = [read_call(), AIMessage(content='done')]
    options = {'profile': {'image_inputs': True}, 'metadata': {MODEL_INPUT_METADATA_KEY: {'api': api}}}
    model = StreamingToolModel(replies=replies, **options) if async_mode else RecordingFake(responses=replies, **options)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    database = tmp_path / 'images.sqlite'
    arguments = dict(tools=[create_session_read_tool()], settings=ToolSettings(), system_prompt='', response_format=None)
    if async_mode:
        async def scenario():
            async with open_async_persistent_application('synthetic', database_path=database, **arguments) as app:
                sid = await app.acreate_workspace_session('A', tmp_path)
                await app.arun(sid, [('user', 'inspect image')])
                first = await app.aget_messages(sid)
            async with open_async_persistent_application('synthetic', database_path=database, **arguments) as app:
                await app.arestore_session(sid)
                return first, await app.aget_messages(sid)
        before, after = asyncio.run(scenario())
    else:
        with SessionStorage(database) as storage:
            app = build_persistent_application('synthetic', storage=storage, **arguments)
            sid = app.create_workspace_session('A', tmp_path)
            before = app.run(sid, [('user', 'inspect image')])['messages']
        with SessionStorage(database) as storage:
            app = build_persistent_application('synthetic', storage=storage, **arguments)
            app.restore_session(sid)
            thread = app._sessions._sessions[sid]['thread_id']
            after = app._agent.get_state({'configurable': {'thread_id': thread}}).values['messages']
    old_tool = next(m for m in before if isinstance(m, ToolMessage))
    restored = next(m for m in after if isinstance(m, ToolMessage))
    assert restored.status == 'success' and restored.tool_call_id == 'image'
    assert restored.content == old_tool.content == json.loads(json.dumps(old_tool.content))
    assert any(b['type'] == 'image' for b in restored.content)
    assert not any(m.additional_kwargs.get('naiyue_image_carrier') for m in after)
    carriers = [m for m in model.seen[1] if m.additional_kwargs.get('naiyue_image_carrier')]
    assert len(carriers) == (1 if api == 'chat_completions' else 0)
    assert len([m for m in after if isinstance(m, HumanMessage)]) == 1


def test_switching_to_vision_model_uses_saved_pixels_without_reading_again(monkeypatch, tmp_path):
    target = tmp_path / 'image.png'; target.write_bytes(png_bytes())
    first = RecordingFake(responses=[read_call(), AIMessage(content='pixels not provided')], profile={'image_inputs': False})
    second = RecordingFake(responses=[AIMessage(content='image inspected')], profile={'image_inputs': True},
                           metadata={MODEL_INPUT_METADATA_KEY: {'api': 'chat_completions'}})
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': first, 'B': second}))
    app = application.build_local_application('synthetic', tools=[create_session_read_tool()],
        settings=ToolSettings(), system_prompt='', response_format=None)
    sid = app.create_workspace_session('A', tmp_path)
    result = app.run(sid, [('user', 'inspect')])
    pixels = next(m for m in result['messages'] if isinstance(m, ToolMessage)).content[-1]['base64']
    assert pixels not in json.dumps([m.content for m in first.seen[1]])
    target.unlink()
    app.select_model(sid, 'B')
    continued = app.run(sid, [('user', 'now inspect the saved image')])
    assert pixels in json.dumps([m.content for m in second.seen[0]])
    assert not any(m.additional_kwargs.get('naiyue_image_carrier') for m in continued['messages'])


@pytest.mark.parametrize('image_in_recent_turn', [False, True])
def test_overflow_retry_rebuilds_carrier_from_canonical_and_summary_omits_pixels(monkeypatch, image_in_recent_turn):
    from test_context_overflow_recovery import _configured_model, _history, _status
    from test_context_compaction import _app
    from test_model_image_messages import history
    model = _configured_model([_status(), AIMessage(content='recovered')])
    model.profile = {'image_inputs': True}
    model.metadata[MODEL_INPUT_METADATA_KEY] = {'api': 'chat_completions'}
    messages = _history()
    image_messages = history()[1:3]
    # This historical batch contains just its image call, so it is fully paired.
    image_messages[0].tool_calls = image_messages[0].tool_calls[:1]
    image_messages[0].id, image_messages[1].id = 'image-ai', 'image-result'
    insertion = 3 if image_in_recent_turn else 1
    messages[insertion:insertion] = image_messages
    app = _app(monkeypatch, {'A': model})
    result = app.run(app.create_session('A'), messages)
    assert result['messages'][-1].content == 'recovered'
    assert len(model.normal_inputs) == 2 and len(model.summary_inputs) == 1
    assert sum(bool(m.additional_kwargs.get('naiyue_image_carrier')) for m in model.normal_inputs[0]) == 1
    assert sum(bool(m.additional_kwargs.get('naiyue_image_carrier')) for m in model.normal_inputs[1]) == int(image_in_recent_turn)
    assert not any(m.additional_kwargs.get('naiyue_image_carrier') for m in result['messages'])
    pixels = image_messages[1].content[-1]['base64']
    summary = model.summary_inputs[0][-1].content
    assert pixels not in summary
    if not image_in_recent_turn:
        assert 'pixels omitted' in summary
        assert not any(isinstance(m.content, list) for m in result['messages'])


def test_image_approval_resumes_once_without_decoder_before_approval(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import read as module
    root = tmp_path / 'workspace'; root.mkdir()
    outside = tmp_path / 'outside.png'; outside.write_bytes(png_bytes())
    model = RecordingFake(responses=[read_call(str(outside)), AIMessage(content='done')], profile={'image_inputs': True})
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = application.build_local_application('synthetic', tools=[create_session_read_tool()],
        settings=ToolSettings(), system_prompt='', response_format=None)
    sid = app.create_workspace_session('A', root)
    entered, real_read = [], module.read_image
    def read(*args, **kwargs):
        entered.append(True)
        return real_read(*args, **kwargs)
    monkeypatch.setattr(module, 'read_image', read)
    paused = app.run(sid, [('user', 'inspect outside image')])
    assert paused['approval_requests'] and entered == []
    resumed = app.resume_tool_approval(sid, ['approve'])
    output = next(m for m in resumed['messages'] if isinstance(m, ToolMessage))
    assert entered == [True] and output.status == 'success' and output.tool_call_id == 'image'
