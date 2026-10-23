"""Formal application loop, two runs, persistent deletion and child release."""

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.run_scope import current_run_scope
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel, call
from test_mcp_connections import facts, process_alive, stdio_settings, until


class ResourceProbeModel(StreamingToolModel):
    probe: Any = None

    async def _astream(self, messages, **kwargs):
        if self.probe is not None:
            await self.probe()
        async for chunk in super()._astream(messages, **kwargs):
            yield chunk


def configured(path, **kwargs):
    from naiyue_test_agent.mcp_client.settings import MCPSettings
    return AppSettings(mcp=MCPSettings(servers={'local': stdio_settings(path, 'stateful')}), **kwargs)


async def increment(sessions, session_id, agent_path='root'):
    async with sessions.borrow_mcp(session_id, agent_path, 'local') as lease:
        result = await lease.client.session.call_tool('echo', {})
        return result.structured_content


@pytest.mark.parametrize('persistent', [False, True])
def test_formal_two_runs_keep_process_and_other_session_isolated(monkeypatch, tmp_path, persistent):
    async def scenario():
        path = tmp_path / 'calls.jsonl'
        outputs = []
        holder = {}
        async def probe():
            services = current_run_scope()
            sessions = holder['app']._sessions
            session_id = next(sid for sid in sessions.loaded_session_ids()
                              if services.observation.publisher.matches(sid, services.observation.publisher.run_id))
            outputs.append(await increment(sessions, session_id))
        model = ResourceProbeModel(replies=[AIMessage(content='ok') for _ in range(3)], probe=probe)
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        options = dict(tools=[], system_prompt='', response_format=None, settings=configured(path))
        async def run(app):
            holder['app'] = app
            a, b = await app.acreate_session('A'), await app.acreate_session('A')
            await app.arun(a, [('user', 'first')])
            await app.arun(a, [('user', 'second')])
            await app.arun(b, [('user', 'independent')])
            assert [row['count'] for row in outputs] == [1, 2, 1]
            assert outputs[0]['pid'] == outputs[1]['pid'] != outputs[2]['pid']
            assert all(process_alive(row['pid']) for row in outputs)
            if persistent:
                await app.adelete_session(a)
                assert not process_alive(outputs[0]['pid']) and process_alive(outputs[2]['pid'])
        if persistent:
            async with open_async_persistent_application('unused', database_path=tmp_path / 'state.sqlite', **options) as app:
                await run(app)
        else:
            app = await build_async_local_application('unused', **options)
            try:
                await run(app)
            finally:
                await app.aclose()
        assert all(not process_alive(row['pid']) for row in outputs)
        assert len([row for row in facts(path) if row['event'] == 'call']) == 3
    asyncio.run(scenario())


def test_real_child_driver_release_does_not_close_root(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import SubAgentSpec, create_subagent_tool
    from test_subagent_execution import record_instances
    async def scenario():
        path = tmp_path / 'child.jsonl'
        outputs, holder, instances = [], {}, []
        async def probe():
            services = current_run_scope()
            instance, = instances
            outputs.append(await increment(holder['app']._sessions, instance['session_id'], instance['agent_path']))
        child = ResourceProbeModel(replies=[AIMessage(content='child done')], probe=probe)
        root = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'resource probe'}), AIMessage(content='done')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
        settings = configured(path, subagents=(SubAgentSpec('tester', 'probe', 'probe', (), model_id='B'),))
        app = await build_async_local_application('unused', tools=[create_subagent_tool()], system_prompt='', response_format=None, settings=settings)
        holder['app'] = app
        instances = record_instances(monkeypatch, app)
        try:
            sid = await app.acreate_session('A')
            root_value = await increment(app._sessions, sid)
            await app.arun(sid, [('user', 'delegate')])
            assert len(outputs) == 1 and not process_alive(outputs[0]['pid'])
            assert process_alive(root_value['pid'])
            from naiyue_test_agent.mcp_client.resources import MCPResourceError
            with pytest.raises(MCPResourceError, match='instance_not_active'):
                async with app._sessions.borrow_mcp(sid, instances[0]['agent_path'], 'local'):
                    pytest.fail('a finished child must not recreate its resources')
            assert (await increment(app._sessions, sid))['count'] == 2
        finally:
            await app.aclose()
        assert not process_alive(root_value['pid'])
    asyncio.run(scenario())


@pytest.mark.parametrize('persistent', [False, True])
def test_assembly_failure_closes_manager_and_temporary_discovery(monkeypatch, tmp_path, persistent):
    from naiyue_test_agent.mcp_client.resources import MCPSessionResources
    async def scenario():
        path = tmp_path / 'startup.jsonl'
        original = MCPSessionResources.aclose
        closed = []
        async def close(self):
            closed.append(self)
            await original(self)
        failure = ValueError('assembly failure')
        def assemble(*args, **kwargs):
            raise failure
        monkeypatch.setattr(MCPSessionResources, 'aclose', close)
        monkeypatch.setattr(application.SessionService, 'assemble_runtime', assemble)
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': StreamingToolModel(replies=[])}))
        with pytest.raises(ValueError) as caught:
            options = dict(tools=[], system_prompt='', response_format=None, settings=configured(path))
            if persistent:
                async with open_async_persistent_application('unused', database_path=tmp_path / 'failed.sqlite', **options):
                    pytest.fail('assembly must fail')
            else:
                await build_async_local_application('unused', **options)
        assert caught.value is failure and len(closed) == 1
        # 04 discovers enabled servers during assembly; temporary clients must close.
        assert facts(path) and not any(row['event'] == 'call' for row in facts(path))
        assert all(not process_alive(row['pid']) for row in facts(path) if row['event'] == 'start')
    asyncio.run(scenario())


def test_formal_user_stop_during_mcp_rpc_cleans_process(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'active-stop.jsonl'
        holder = {}
        async def probe():
            async with holder['app']._sessions.borrow_mcp(holder['sid'], 'root', 'local') as lease:
                await lease.client.session.call_tool('echo', {'text': 'wait'})
        model = ResourceProbeModel(replies=[AIMessage(content='must not complete')], probe=probe)
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        app = await build_async_local_application('unused', tools=[], system_prompt='', response_format=None, settings=configured(path))
        holder.update(app=app, sid=await app.acreate_session('A'))
        task = asyncio.create_task(app.arun(holder['sid'], [('user', 'wait')]))
        try:
            await until(lambda: any(row['event'] == 'call' for row in facts(path)))
            app.request_stop(holder['sid'])
            result = await asyncio.wait_for(task, 15)
            assert result['run_stopped']
            assert all(not process_alive(row['pid']) for row in facts(path) if row['event'] == 'start')
            assert len([r for r in facts(path) if r['event'] == 'call']) == 1
        finally:
            await app.aclose()
    asyncio.run(scenario())
