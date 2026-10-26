"""Read child messages through the owning session without executing any graph."""

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from test_subagent_execution import ChildModel, delegated_app, record_instances
from test_async_application import call
from test_dynamic_tools import definition


@async_test
async def test_child_history_outlives_live_instance_and_cannot_cross_sessions(monkeypatch):
    child = ChildModel(replies=[call('probe', {'value': 'read'}, 'inner'), AIMessage(content='full child report')])
    app, root = await delegated_app(monkeypatch, child, tools=[definition()])
    instances = record_instances(monkeypatch, app)
    sid = await app.host.sessions.create('A')
    await app.host.runs.start(sid, [('user', 'start')])
    before = (len(root.seen), len(child.seen))
    assert all(not (owner._runtime.run.subagent_instances if owner._runtime.run else {}) for owner in app.host.sessions._sessions._sessions.values())
    records = await app.host.history.subagents(sid)
    record, = records
    assert record['agent_path'] == instances[0]['agent_path']
    assert record['status'] == 'completed' and record['task'] == 'check this task'
    history = await app.host.history.subagent(sid, record['agent_id'])
    assert history['available'] and [m.type for m in history['messages']] == ['human', 'ai', 'tool', 'ai']
    assert history['messages'][-1].content == 'full child report'
    history['messages'][-1].content = 'changed outside'
    assert (await app.host.history.subagent(sid, record['agent_id']))['messages'][-1].content == 'full child report'
    other = await app.host.sessions.create('A')
    with pytest.raises(LookupError):
        await app.host.history.subagent(other, record['agent_id'])
    with pytest.raises(LookupError):
        await app.host.history.subagent(sid, 'subagent:' + record['agent_id'])
    assert (len(root.seen), len(child.seen)) == before
    await app.aclose()


@async_test
async def test_not_started_child_is_empty_without_a_fabricated_checkpoint(monkeypatch):
    from test_async_application import StreamingToolModel
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'missing', 'task': 'unstarted'}, 'outer'),
                                      AIMessage(content='root done')])
    app, _ = await delegated_app(monkeypatch, ChildModel(replies=[]), root=root)
    sid = await app.host.sessions.create('A')
    await app.host.runs.start(sid, [('user', 'start')])
    record, = await app.host.history.subagents(sid)
    detail = await app.host.history.subagent(sid, record['agent_id'])
    assert not detail['available'] and detail['messages'] == []
    assert detail['status'] == 'failed'
    await app.aclose()
