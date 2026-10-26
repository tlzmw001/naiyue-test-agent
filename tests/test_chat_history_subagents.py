"""Child history is scoped to its real root delegation, including after a crash."""

import json

from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from naiyue_test_agent import application
from scripts.component_fixtures import open_component_application
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel, call
from test_subagent_execution import ChildModel
from test_subagent_restart import options
from test_chat_history import rows


@async_test
async def test_root_child_archives_stay_separate_and_delete_together(monkeypatch, tmp_path):
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': 'one'}, 'outer'),
        AIMessage(content='root done'), call('subagent', {'agent': 'worker', 'task': 'two'}, 'outer'),
        AIMessage(content='second root')])
    child = ChildModel(replies=[AIMessage(content='first child'), AIMessage(content='second child')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    async with open_component_application('unused', **options(tmp_path)) as app:
        sid, other = await app.host.sessions.create_workspace('A', tmp_path), await app.host.sessions.create_workspace('A', tmp_path)
        await app.host.runs.start(sid, [('user', 'delegate one')])
        await app.host.runs.start(other, [('user', 'delegate two')])
        storage = app.host.sessions._persistence.storage
        head = next(r for r in storage.conversation.chat_heads(sid) if r['agent_path'] != 'root')
        own = rows(storage, sid)
        root_rows = [r for r in own if r['thread_id'] == storage.sessions.get(sid)['thread_id']]
        child_rows = [r for r in own if r['thread_id'] == head['thread_id']]
        assert [json.loads(r['body_json'])['data']['content'] for r in child_rows] == ['one', 'first child']
        parent_tool = json.loads(root_rows[2]['body_json'])['data']
        assert parent_tool['artifact']['agent_path'] == head['agent_path']
        assert parent_tool['artifact']['status'] == 'completed'
        assert head['thread_id'].startswith('subagent:') and head['agent_path'].startswith('root/worker/')
        assert head['thread_id'] not in {r['thread_id'] for r in rows(storage, other)}
        untouched = rows(storage, other)
        await app.host.sessions.delete(sid)
        assert rows(storage, sid) == [] and storage.conversation.chat_heads(sid) == []
        assert rows(storage, other) == untouched
        assert storage.database.connection.execute('SELECT count(*) FROM checkpoints WHERE thread_id=?', (head['thread_id'],)).fetchone()[0] == 0
