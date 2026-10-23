"""Shared SQLite keeps root durability independent from local child outcomes."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_subagent_tool
from naiyue_test_agent.settings import AppSettings
from native_application_helpers import async_test
from test_async_application import StreamingToolModel, call
from test_subagent_execution import ChildModel, record_instances, role
from test_dynamic_tools import definition


@pytest.mark.parametrize('fail_child', [True, False])
@async_test
async def test_child_save_failure_or_success_does_not_change_root_save_contract(monkeypatch, tmp_path, fail_child):
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'check'}, 'outer'),
                                       AIMessage(content='root done')])
    child = ChildModel(replies=[AIMessage(content='saved child report')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    options = dict(database_path=tmp_path / 'state.sqlite', tools=[create_subagent_tool(), definition()],
        initial_active_tools=['subagent'], system_prompt='', response_format=None, settings=AppSettings(subagents=(role(),)))
    events = []
    async with open_async_persistent_application('unused', **options) as app:
        instances = record_instances(monkeypatch, app)
        app.observability.subscribe(events.append)
        sid = await app.sessions.create_workspace('A', tmp_path)
        backend = app.runs._agent.checkpointer
        save = backend.aput
        error = OSError('PRIVATE_PERSISTENCE_BODY')
        async def fail(config, *args, **kwargs):
            if config['configurable']['thread_id'].startswith('subagent:'): raise error
            return await save(config, *args, **kwargs)
        if fail_child:
            monkeypatch.setattr(backend, 'aput', fail)
            result = await app.runs.start(sid, [('user', 'run')])
            assert result['messages'][-1].content == 'root done'
            assert (await app.sessions.list())[0]['state'] == 'ready'
            feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert feedback.status == 'error' and 'checkpoint' in feedback.content.lower()
            assert instances[0]['status'] == 'failed' and instances[0]['settled']
        else:
            async def fail_root_save(*args): raise error
            monkeypatch.setattr(app.sessions._persistence, 'afinish', fail_root_save)
            with pytest.raises(OSError) as caught: await app.runs.start(sid, [('user', 'run')])
            assert caught.value is error
            assert instances[0]['status'] == 'completed' and instances[0]['settled']
            assert [(e.source is None, e.data.status) for e in events if e.event_type == 'run_finished'] == [
                (False, 'completed'), (True, 'failed')]
            assert not app.sessions._sessions.require(sid).describe_session()['busy'] and all(not (owner._run.subagent_instances if owner._run else {}) for owner in app.sessions._sessions._sessions.values())
    if fail_child:
        async with open_async_persistent_application('unused', **options) as restored:
            await restored.sessions.restore(sid)
            messages = await restored.history.messages(sid)
            assert messages[-1].content == 'root done'
            feedback = next(m for m in messages if isinstance(m, ToolMessage))
            assert feedback.status == 'error' and 'checkpoint' in feedback.content.lower()
