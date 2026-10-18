"""Child history is scoped to its real root delegation, including after a crash."""

import json

from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
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
    async with open_async_persistent_application('unused', **options(tmp_path)) as app:
        sid, other = await app.acreate_workspace_session('A', tmp_path), await app.acreate_workspace_session('A', tmp_path)
        await app.arun(sid, [('user', 'delegate one')])
        await app.arun(other, [('user', 'delegate two')])
        storage = app._storage
        head = next(r for r in storage.chat_heads(sid) if r['agent_path'] != 'root')
        own = rows(storage, sid)
        root_rows = [r for r in own if r['thread_id'] == storage.get(sid)['thread_id']]
        child_rows = [r for r in own if r['thread_id'] == head['thread_id']]
        assert [json.loads(r['body_json'])['data']['content'] for r in child_rows] == ['one', 'first child']
        parent_tool = json.loads(root_rows[2]['body_json'])['data']
        assert parent_tool['artifact']['agent_path'] == head['agent_path']
        assert parent_tool['artifact']['status'] == 'completed'
        assert head['thread_id'].startswith('subagent:') and head['agent_path'].startswith('root/worker/')
        assert head['thread_id'] not in {r['thread_id'] for r in rows(storage, other)}
        untouched = rows(storage, other)
        await app.adelete_session(sid)
        assert rows(storage, sid) == [] and storage.chat_heads(sid) == []
        assert rows(storage, other) == untouched
        assert storage.connection.execute('SELECT count(*) FROM checkpoints WHERE thread_id=?', (head['thread_id'],)).fetchone()[0] == 0
