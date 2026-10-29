"""Root status cannot leak to child prompts, manual summaries or unsent inputs."""
from naiyue_test_agent.application.sessions.session_execution import RunLifecycle

import asyncio

from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel
from test_background_tools import background_app
from test_background_notices import completed
from test_background_notice_delivery import system_text


def test_child_does_not_inherit_status_and_later_completion_is_not_acknowledged(monkeypatch, tmp_path):
    from test_subagent_execution import ChildModel, delegated_app
    from naiyue_test_agent.tools import SubAgentSpec
    async def scenario():
        child = ChildModel(replies=[AIMessage(content='child done')])
        app, root = await delegated_app(monkeypatch, child,
            spec=SubAgentSpec('tester', 'Check', 'child role', (), model_id='B'))
        try:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            manager = app.host.sessions._sessions.background_tasks
            first, = await completed(manager, tmp_path, sid=sid)
            later = []
            async def finish_in_child(_messages):
                later.extend(await completed(manager, tmp_path, sid=sid, prefix='later'))
            child.on_request = finish_in_child
            await app.host.runs.start(sid, [('user', 'delegate')])
            assert first in system_text(root.seen[0]) and first in system_text(root.seen[1])
            assert first not in system_text(child.seen[0]) and later[0] not in system_text(child.seen[0])
            assert all(service.background_notices is None for service in child.services)
            pending = manager.notice_snapshot(session_id=sid, workspace_root=tmp_path)
            assert [identity for identity, _ in pending.versions] == later
            root.replies.append(AIMessage(content='next'))
            await app.host.runs.start(sid, [('user', 'next')])
            assert later[0] in system_text(root.seen[2]) and first not in system_text(root.seen[2])
        finally:
            await app.aclose()
    from native_application_helpers import async_test
    async_test(scenario)()


def test_manual_compaction_does_not_collect_or_acknowledge_status(monkeypatch, tmp_path):
    from test_manual_compaction import compact_model
    async def scenario():
        model = compact_model()
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            await app.host.runs.start(sid, [('user', 'old task')])
            await app.host.runs.start(sid, [('user', 'recent task')])
            manager = app.host.sessions._sessions.background_tasks
            identity, = await completed(manager, tmp_path, sid=sid)
            compacted = await app.host.runs.compact(sid)
            assert compacted['compaction_status'] == 'applied' and len(model.seen) == 3
            assert identity not in system_text(model.seen[2])
            assert manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
            model.replies.append(AIMessage(content='next'))
            await app.host.runs.start(sid, [('user', 'next')])
            assert identity in system_text(model.seen[3])
    asyncio.run(scenario())


def test_snapshot_without_actual_system_part_is_not_acknowledged(monkeypatch, tmp_path):
    from naiyue_test_agent.application.sessions.agent_session import AgentSession
    original = RunLifecycle.resolve_prompt_parts
    def without_status(self, agent_path='root'):
        return [part for part in original(self, agent_path) if part['id'] != 'background_tasks']
    monkeypatch.setattr(RunLifecycle, 'resolve_prompt_parts', without_status)
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='success')])
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            manager = app.host.sessions._sessions.background_tasks
            identity, = await completed(manager, tmp_path, sid=sid)
            result = await app.host.runs.start(sid, [('user', 'run')])
            assert result['messages'][-1].content == 'success'
            assert identity not in system_text(model.seen[0])
            assert manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
    asyncio.run(scenario())


def test_preview_rejects_changed_notice_version_without_sending_model(monkeypatch, tmp_path):
    from naiyue_test_agent.application.sessions import session_context
    original = session_context.estimate_context
    async def scenario():
        model = StreamingToolModel(replies=[])
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            manager = app.host.sessions._sessions.background_tasks
            await completed(manager, tmp_path, sid=sid)
            notice = manager.notice_snapshot(session_id=sid, workspace_root=tmp_path)
            def changed(*args):
                result = original(*args)
                manager.acknowledge_notices(notice, session_id=sid, workspace_root=tmp_path, run_id=None)
                return result
            monkeypatch.setattr(session_context, 'estimate_context', changed)
            view = await app.host.history.context(sid)
            assert view['current'] == {'basis': 'unavailable', 'estimate': None}
            assert model.seen == []
    asyncio.run(scenario())


def test_automatic_compaction_budget_uses_the_same_status_segment(monkeypatch, tmp_path):
    from naiyue_test_agent.agent_runtime import request_preparation
    async def scenario():
        seen = []
        original = request_preparation._acompact_messages
        async def capture(*args, **kwargs):
            seen.append(kwargs['system_messages'][0].content)
            return await original(*args, **kwargs)
        monkeypatch.setattr(request_preparation, '_acompact_messages', capture)
        model = StreamingToolModel(replies=[AIMessage(content='done')])
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            identity, = await completed(app.host.sessions._sessions.background_tasks, tmp_path, sid=sid)
            await app.host.runs.start(sid, [('user', 'run')])
            assert len(seen) == 1 and identity in seen[0]
            assert seen[0] == system_text(model.seen[0])
    asyncio.run(scenario())


def test_snapshot_freezes_before_project_resource_io(monkeypatch, tmp_path):
    from threading import Event
    from naiyue_test_agent.application.sessions import session_execution
    async def scenario():
        entered, release = asyncio.Event(), Event()
        original = session_execution.prepare_resources
        loop = asyncio.get_running_loop()
        async def blocked(*args, **kwargs):
            entered.set()
            assert await asyncio.to_thread(release.wait,5)
            return await original(*args, **kwargs)
        monkeypatch.setattr(session_execution, 'prepare_resources', blocked)
        model = StreamingToolModel(replies=[AIMessage(content='first'), AIMessage(content='next')])
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            pending = asyncio.create_task(app.host.runs.start(sid, [('user', 'first')]))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                manager = app.host.sessions._sessions.background_tasks
                identity, = await completed(manager, tmp_path, sid=sid)
                release.set()
                await pending
                assert identity not in system_text(model.seen[0])
                assert manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
                await app.host.runs.start(sid, [('user', 'next')])
                assert identity in system_text(model.seen[1])
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
    asyncio.run(scenario())


def test_preview_checks_notice_version_after_last_checkpoint_read(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[])
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            manager = app.host.sessions._sessions.background_tasks
            await completed(manager, tmp_path, sid=sid)
            original = app.host.runs._agent.aget_state
            reads = []
            async def changed(*args, **kwargs):
                result = await original(*args, **kwargs)
                reads.append(1)
                if len(reads) == 2:
                    await completed(manager, tmp_path, sid=sid, prefix='during-read')
                return result
            monkeypatch.setattr(app.host.runs._agent, 'aget_state', changed)
            view = await app.host.history.context(sid)
            assert view['current'] == {'basis': 'unavailable', 'estimate': None}
            assert model.seen == []
    asyncio.run(scenario())
