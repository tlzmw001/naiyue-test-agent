"""Saved instances keep their identity across prompt and reply lifecycle changes."""

import json
import importlib
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from native_application_helpers import async_test
from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.application.conversation.reply_storage import latest
from naiyue_test_agent.application.errors import SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import SubAgentSpec
from naiyue_test_agent.tools.permission_rules import PermissionRule, PermissionSettings
from test_async_application import StreamingToolModel, call
from test_subagent_continuation import feedback
from test_subagent_execution import ChildModel
from test_subagent_restart import options


def models(monkeypatch, root, child):
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))


async def delegate(app, sid, root, *, task='first', identity=None):
    args = dict(agent='worker', task=task)
    if identity is not None:
        args['agent_id'] = identity
    root.replies.extend([call('subagent', args, uuid4().hex), AIMessage(content='root done')])
    return feedback(await app.runs.start(sid, [('user', task)]))


async def replace(app, sid):
    boundary = app.sessions._persistence.storage.replies.latest(sid)
    return await app.runs.regenerate(sid, request_id=str(uuid4()),
        expected_reply_id=boundary['reply_id'], expected_conversation_revision=app.sessions._persistence.storage.sessions.get(sid)['conversation_revision'])


@async_test
async def test_normal_reopen_is_passive_and_explicit_continuation_keeps_child_history(monkeypatch, tmp_path):
    root, child = StreamingToolModel(replies=[]), ChildModel(replies=[AIMessage(content='first report'), AIMessage(content='second report')])
    models(monkeypatch, root, child)
    async with open_async_persistent_application('unused', **options(tmp_path)) as app:
        sid = await app.sessions.create('A')
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        saved = app.sessions._persistence.storage.subagents.get_subagent(sid, identity)
    before = (len(root.seen), len(child.seen))
    async with open_async_persistent_application('unused', **options(tmp_path)) as app:
        events = []
        app.observability.subscribe(events.append)
        await app.sessions.restore(sid)
        assert (len(root.seen), len(child.seen)) == before and not events
        owner = app.sessions._sessions.require(sid)
        assert owner._runtime.run is None and (await owner.subagents.asubagent_references())[identity] == saved
        reply = await delegate(app, sid, root, task='second', identity=identity)
        assert json.loads(reply.content)['report'] == 'second report'
        current = app.sessions._persistence.storage.subagents.get_subagent(sid, identity)
        assert current['state'] == 'ready' and current['child_thread_id'] == saved['child_thread_id']
        assert current['last_delegation_id'] != saved['last_delegation_id']
        assert [m.content for m in child.seen[-1] if isinstance(m, HumanMessage)] == ['first', 'second']
        assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'completed'


@async_test
async def test_root_compaction_keeps_registry_list_child_archive_and_continuation(monkeypatch, tmp_path):
    root = StreamingToolModel(replies=[], metadata={
        'naiyue_model_limits': {'context_window_tokens': 128000, 'max_output_tokens': 1000},
        'naiyue_compaction_reserve': {'tokens': 2000}, 'naiyue_compaction_keep_recent': {'tokens': 20000},
        'naiyue_compaction_summary_output': {'tokens': 256}})
    child = ChildModel(replies=[AIMessage(content='old details ' * 600), AIMessage(content='continued')])
    models(monkeypatch, root, child)
    async with open_async_persistent_application('unused', **options(tmp_path)) as app:
        sid = await app.sessions.create('A')
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        saved = app.sessions._persistence.storage.subagents.get_subagent(sid, identity)
        root.replies.extend([AIMessage(content='recent answer'), AIMessage(content='Earlier delegated work completed.')])
        await app.runs.start(sid, [('user', 'recent')])
        await app.runs.compact(sid)
        messages = await app.history.messages(sid)
        assert messages[0].additional_kwargs['naiyue_compaction_summary']
        assert not any(getattr(m, 'tool_calls', []) for m in messages)
        assert [r['agent_id'] for r in await app.history.subagents(sid)] == [identity]
        assert [r['agent_id'] for r in await app.history.chat_subagents(sid)] == [identity]
        assert app.sessions._persistence.storage.subagents.get_subagent(sid, identity) == saved
        assert (await app.history.subagent(sid, identity))['messages'][-1].content == 'old details ' * 600
        assert json.loads((await delegate(app, sid, root, task='second', identity=identity)).content)['report'] == 'continued'
        assert len(child.seen) == 2


@async_test
async def test_regeneration_retires_touched_prefix_instance_but_keeps_untouched_and_history(monkeypatch, tmp_path):
    root = StreamingToolModel(replies=[])
    child = ChildModel(replies=[AIMessage(content=x) for x in ('original', 'untouched', 'discarded future', 'still usable')])
    models(monkeypatch, root, child)
    async with open_async_persistent_application('unused', **options(tmp_path)) as app:
        sid = await app.sessions.create('A')
        touched = json.loads((await delegate(app, sid, root)).content)['agent_id']
        untouched = json.loads((await delegate(app, sid, root, task='unrelated')).content)['agent_id']
        await delegate(app, sid, root, task='discard me', identity=touched)
        before = app.sessions._persistence.storage.subagents.get_subagent(sid, touched)
        root.replies.append(AIMessage(content='replacement without delegation'))
        await replace(app, sid)
        row = app.sessions._persistence.storage.subagents.get_subagent(sid, touched)
        assert row['state'] == 'retired' and row['version'] > before['version']
        assert row['checkpoint_id'] == before['checkpoint_id']
        assert app.sessions._persistence.storage.subagents.get_subagent(sid, untouched)['state'] == 'ready'
        assert (await app.history.subagent(sid, touched))['messages'][-1].content == 'discarded future'
        denied = await delegate(app, sid, root, task='cannot use future', identity=touched)
        assert denied.status == 'error' and 'retired' in denied.content
        assert len(child.seen) == 3
        assert json.loads((await delegate(app, sid, root, task='continue other', identity=untouched)).content)['report'] == 'still usable'


@async_test
async def test_regeneration_orphan_clears_pointer_and_cannot_resurface_in_list(monkeypatch, tmp_path):
    root, child = StreamingToolModel(replies=[]), ChildModel(replies=[AIMessage(content='discarded only')])
    models(monkeypatch, root, child)
    async with open_async_persistent_application('unused', **options(tmp_path)) as app:
        sid = await app.sessions.create('A')
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        root.replies.append(AIMessage(content='replacement'))
        await replace(app, sid)
        row = app.sessions._persistence.storage.subagents.get_subagent(sid, identity)
        assert row['state'] == 'retired' and row['checkpoint_id'] is None
        assert await app.history.subagents(sid) == [] and await app.history.chat_subagents(sid) == []
        assert all(h['thread_id'] != row['child_thread_id'] for h in app.sessions._persistence.storage.conversation.chat_heads(sid))
        assert not app.sessions._persistence.storage.database.read_rows('SELECT 1 FROM checkpoints WHERE thread_id=?', (row['child_thread_id'],))
        with pytest.raises(LookupError):
            await app.history.subagent(sid, identity)


@async_test
async def test_regeneration_retirement_failure_rolls_back_entire_activation(monkeypatch, tmp_path):
    root, child = StreamingToolModel(replies=[]), ChildModel(replies=[AIMessage(content='saved')])
    models(monkeypatch, root, child)
    async with open_async_persistent_application('unused', **options(tmp_path)) as app:
        sid = await app.sessions.create('A')
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        before = app.sessions._persistence.storage.subagents.get_subagent(sid, identity)
        parent = app.sessions._persistence.storage.sessions.get(sid)
        archive = app.sessions._persistence.storage.database.read_rows('SELECT * FROM naiyue_chat_messages WHERE session_id=?', (sid,))
        app.sessions._persistence.storage.database.connection.execute("""CREATE TRIGGER reject_retirement BEFORE UPDATE ON naiyue_subagent_instances
            WHEN NEW.state='retired' BEGIN SELECT RAISE(ABORT,'PRIVATE_RETIREMENT'); END""")
        root.replies.append(AIMessage(content='must not reach replacement model'))
        with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
            await replace(app, sid)
        assert app.sessions._persistence.storage.sessions.get(sid)['thread_id'] == parent['thread_id']
        assert app.sessions._persistence.storage.subagents.get_subagent(sid, identity) == before
        assert app.sessions._persistence.storage.database.read_rows('SELECT * FROM naiyue_chat_messages WHERE session_id=?', (sid,)) == archive
        assert len(root.seen) == 2 and len(child.seen) == 1 and not app.sessions._sessions.require(sid).is_busy()


@pytest.mark.parametrize('changed', ['role', 'root_model'])
@async_test
async def test_reopen_rechecks_role_and_keeps_frozen_child_model(monkeypatch, tmp_path, changed):
    root, child = StreamingToolModel(replies=[]), ChildModel(replies=[AIMessage(content='saved'), AIMessage(content='same child model')])
    models(monkeypatch, root, child)
    config = options(tmp_path)
    async with open_async_persistent_application('unused', **config) as app:
        sid = await app.sessions.create('A')
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
    if changed == 'role':
        spec = config['settings'].subagents[0]
        config['settings'] = config['settings'].model_copy(update={'subagents': (
            SubAgentSpec(spec.name, spec.description, 'Changed role instructions.', spec.tool_names, model_id='B'),)})
    other = StreamingToolModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child, 'C': other}))
    async with open_async_persistent_application('unused', **config) as app:
        await app.sessions.restore(sid)
        if changed == 'root_model':
            await app.sessions.select_model(sid, 'C')
        reply = await delegate(app, sid, root if changed == 'role' else other, task='new', identity=identity)
        if changed == 'role':
            assert reply.status == 'error' and 'role configuration changed' in reply.content and len(child.seen) == 1
        else:
            assert json.loads(reply.content)['report'] == 'same child model' and len(child.seen) == 2
            assert app.sessions._persistence.storage.subagents.get_subagent(sid, identity)['model_id'] == 'B'


@async_test
async def test_denied_continuation_does_not_retire_the_last_successful_instance(monkeypatch, tmp_path):
    root, child = StreamingToolModel(replies=[]), ChildModel(replies=[AIMessage(content='saved'), AIMessage(content='still usable')])
    models(monkeypatch, root, child)
    async with open_async_persistent_application('unused', **options(tmp_path)) as app:
        sid = await app.sessions.create('A')
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        saved = app.sessions._persistence.storage.subagents.get_subagent(sid, identity)
        # A mismatching role is rejected before claim and must not alter actual delegation ownership.
        root.replies.extend([call('subagent', {'agent': 'missing', 'task': 'denied', 'agent_id': identity}, 'bad'), AIMessage(content='denied')])
        assert feedback(await app.runs.start(sid, [('user', 'denied request')])).status == 'error'
        root.replies.append(AIMessage(content='replacement'))
        await replace(app, sid)
        assert app.sessions._persistence.storage.subagents.get_subagent(sid, identity) == saved
        assert (await app.history.chat_subagents(sid))[0]['status'] == 'completed'
        assert json.loads((await delegate(app, sid, root, task='new', identity=identity)).content)['report'] == 'still usable'


class CompactingChild(ChildModel):
    summaries: list = []

    async def _next(self, messages):
        if messages and str(messages[0].content).startswith('Summarize completed'):
            self.summaries.append(messages)
            return AIMessage(content='Earlier child work was completed.')
        return await super()._next(messages)


@async_test
async def test_child_compaction_keeps_thread_archive_and_current_delegation_budget(monkeypatch, tmp_path):
    workspace = tmp_path / 'work'
    workspace.mkdir()
    (workspace / 'input.txt').write_text('readable')
    root = StreamingToolModel(replies=[])
    child = CompactingChild(replies=[AIMessage(content='old details ' * 600), AIMessage(content='second'),
        call('read', {'path': 'input.txt'}, 'read'), AIMessage(content='third report')])
    models(monkeypatch, root, child)
    config = options(tmp_path)
    spec = config['settings'].subagents[0]
    config['settings'] = config['settings'].model_copy(update={'subagents': (
        SubAgentSpec(spec.name, spec.description, spec.system_prompt, spec.tool_names, model_id='B', max_model_calls=2),)})
    async with open_async_persistent_application('unused', **config) as app:
        sid = await app.sessions.create_workspace('A', workspace)
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        await delegate(app, sid, root, task='second', identity=identity)
        child.metadata = {'naiyue_model_limits': {'context_window_tokens': 2200, 'max_output_tokens': 100},
            'naiyue_compaction_reserve': {'tokens': 1200}, 'naiyue_compaction_keep_recent': {'tokens': 10},
            'naiyue_compaction_summary_output': {'tokens': 100}}
        reply = await delegate(app, sid, root, task='read input and report', identity=identity)
        assert json.loads(reply.content)['report'] == 'third report'
        thread = 'subagent:' + identity
        snapshot = await app.sessions._sessions._subagents['worker']['agent'].aget_state({'configurable': {'thread_id': thread}})
        assert child.summaries and snapshot.values['messages'][0].additional_kwargs['naiyue_compaction_summary']
        assert snapshot.values['thread_model_call_count'] == 2 and len(child.seen) == 4
        record = app.sessions._persistence.storage.subagents.get_subagent(sid, identity)
        assert record['state'] == 'ready' and record['checkpoint_id'] == snapshot.config['configurable']['checkpoint_id']
        archive = app.sessions._persistence.storage.database.read_rows('SELECT message_id FROM naiyue_chat_messages WHERE session_id=? AND thread_id=?', (sid, thread))
        assert len(archive) > len(snapshot.values['messages'])


@async_test
async def test_reopened_instance_executes_under_current_child_permissions(monkeypatch, tmp_path):
    workspace = tmp_path / 'work'
    workspace.mkdir()
    root = StreamingToolModel(replies=[])
    child = ChildModel(replies=[AIMessage(content='saved'), call('read', {'path': 'private.txt'}, 'read'), AIMessage(content='access denied')])
    models(monkeypatch, root, child)
    config = options(tmp_path)
    async with open_async_persistent_application('unused', **config) as app:
        sid = await app.sessions.create_workspace('A', workspace)
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
    config['settings'] = config['settings'].model_copy(update={
        'permissions': PermissionSettings(deny=(PermissionRule(tool='read'),))})
    reached = []
    def forbidden(*args, **kwargs):
        reached.append(True)
        raise AssertionError('Current deny must prevent actual file IO')
    monkeypatch.setattr(importlib.import_module('naiyue_test_agent.tools.read'), '_read', forbidden)
    async with open_async_persistent_application('unused', **config) as app:
        await app.sessions.restore(sid)
        assert json.loads((await delegate(app, sid, root, task='read private file', identity=identity)).content)['report'] == 'access denied'
        tool_result = child.seen[-1][-1]
        assert tool_result.type == 'tool' and tool_result.status == 'error' and tool_result.tool_call_id == 'read'
        assert not reached and len(child.seen) == 3


@async_test
async def test_root_approval_resume_keeps_logical_reply_for_instance_retirement(monkeypatch, tmp_path):
    workspace = tmp_path / 'work'
    workspace.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('requires approval')
    root, child = StreamingToolModel(replies=[]), ChildModel(replies=[AIMessage(content='prefix'), AIMessage(content='discarded future')])
    models(monkeypatch, root, child)
    config = options(tmp_path)
    config['initial_active_tools'] = ['subagent', 'read']
    async with open_async_persistent_application('unused', **config) as app:
        sid = await app.sessions.create_workspace('A', workspace)
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        root.replies.extend([call('read', {'path': str(outside)}, 'outer-read'),
            call('subagent', {'agent': 'worker', 'task': 'discard me', 'agent_id': identity}, 'continued'), AIMessage(content='old reply')])
        pending = await app.runs.start(sid, [('user', 'read outside then continue child')])
        assert pending['__interrupt__'] and len(child.seen) == 1
        await app.runs.resume_approval(sid, ['approve'])
        boundary = app.sessions._persistence.storage.replies.latest(sid)
        record = app.sessions._persistence.storage.subagents.get_subagent(sid, identity)
        assert record['last_parent_reply_id'] == boundary['reply_id']
        root.replies.append(AIMessage(content='replacement'))
        await replace(app, sid)
        assert app.sessions._persistence.storage.subagents.get_subagent(sid, identity)['state'] == 'retired'
        assert (await app.history.subagent(sid, identity))['messages'][-1].content == 'discarded future'
