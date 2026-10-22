"""Real continuation acceptance shared by source tests and installed smoke."""

from contextlib import asynccontextmanager
import importlib
import json
import re
from unittest.mock import patch


def verify_subagent_report(report):
    """The installer rejects missing or inconsistent facts before writing success."""
    def require(condition):
        if not condition:
            raise ValueError('Subagent acceptance facts are missing or inconsistent')
    try:
        rows = report['continuations']
        require(len(rows) == 3)
        identity = rows[0]['agent_id']
        require(isinstance(identity, str) and re.fullmatch('[0-9a-f]{64}', identity) is not None)
        for index, row in enumerate(rows, 1):
            require(row['agent_id'] == identity and row['child_thread_id'] == 'subagent:' + identity)
            require(type(row['model_calls']) is int and row['model_calls'] == 2)
            require(row['budget_count'] == 2 and row['write_calls'] == index)
            require(row['previous_reports'] == [f'report-{i}' for i in range(1, index)])
            require(row['report'] == f'report-{index}' and row['file_content'] == f'effect-{index}')
        for key in ('checkpoint_id', 'delegation_id'):
            require(all(isinstance(row[key], str) and row[key] for row in rows))
            require(len({row[key] for row in rows}) == 3)
        fresh = report['fresh']
        require(re.fullmatch('[0-9a-f]{64}', fresh['agent_id']) is not None and fresh['agent_id'] != identity)
        require(fresh['child_thread_id'] == 'subagent:' + fresh['agent_id'])
        require(fresh['previous_reports'] == [] and fresh['model_calls'] == fresh['budget_count'] == 2)
        require(fresh['report'] == 'fresh-report' and fresh['file_content'] == 'fresh-effect' and fresh['write_calls'] == 4)
        require(report['root_model_calls'] == 12 and report['child_model_calls'] == 8 and report['write_calls'] == 4)
        require(report['reopen'] == dict(model_calls=0, write_calls=0, auto_calls=0,
            drivers=0, approvals=0, history_ids_equal=True))
        require(len(report['lists']) == 2)
        for row in report['lists']:
            require(row['agent_id'] == identity and row['resume_available'] is True and row['child_model_calls'] == 0)
            require(0 < row['bytes'] <= 8192)
        require(report['provider_requests'] == 0)
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise ValueError('Subagent acceptance facts are missing or inconsistent') from error


def call(name, args, identity):
    from langchain_core.messages import AIMessage
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': identity}])


def tool_result(result, name):
    from langchain_core.messages import ToolMessage
    return next(message for message in reversed(result['messages'])
        if isinstance(message, ToolMessage) and message.name == name)


async def subagent_scenario(root, Model):
    """Only pass a disposable root; every write runs through the real tool graph."""
    from langchain_core.messages import AIMessage
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_persistent import open_async_persistent_application
    from naiyue_test_agent.agent_runtime.tool_execution.permissions import AutoPermissionReviewer
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import SubAgentSpec, create_builtin_tools

    directory = root.resolve(strict=True) / 'subagent-continuation'
    directory.mkdir()
    workspace = directory / 'work'
    workspace.mkdir()
    settings = AppSettings(subagents=(SubAgentSpec('worker', 'Write the requested file',
        'Write once and report the result.', ('write',), model_id='child', max_model_calls=2),))
    tools = [spec for spec in create_builtin_tools(settings.tools) if spec.tool.name in ('subagent', 'subagent_list', 'write')]
    parent, child = Model(replies=[]), Model(replies=[])

    @asynccontextmanager
    async def opened():
        with patch.object(application, 'load_model_catalog', lambda _: ModelCatalog('root', {'root': parent, 'child': child})):
            async with open_async_persistent_application('unused', database_path=directory / 'state.sqlite',
                    tools=tools, initial_active_tools=['subagent'], system_prompt='', response_format=None, settings=settings) as app:
                yield app

    async def delegate(app, sid, index, identity=None):
        fresh = index == 'fresh'
        report = 'fresh-report' if fresh else f'report-{index}'
        content = 'fresh-effect' if fresh else f'effect-{index}'
        filename = 'fresh.txt' if fresh else f'effect-{index}.txt'
        args = dict(agent='worker', task=f'Write {filename} and return {report}.')
        if identity is not None:
            args['agent_id'] = identity
        parent.replies.extend([call('subagent', args, f'delegate-{index}'), AIMessage(content=f'parent-{index}')])
        child.replies.extend([call('write', dict(path=filename, content=content), f'write-{index}'), AIMessage(content=report)])
        before = len(child.seen)
        result = await app.arun(sid, [('user', args['task'])])
        feedback = tool_result(result, 'subagent')
        assert feedback.status == 'success', feedback.content
        envelope = json.loads(feedback.content)
        assert envelope['report'] == report and envelope['status'] == 'completed' and not envelope['truncated']
        identity = envelope['agent_id']
        record = app._storage.get_subagent(sid, identity)
        graph = app._sessions._subagents['worker']['agent']
        state = await graph.aget_state({'configurable': {'thread_id': record['child_thread_id']}})
        assert record['state'] == 'ready' and state.config['configurable']['checkpoint_id'] == record['checkpoint_id']
        assert tool_result(state.values, 'write').status == 'success'
        previous = [m.content for m in child.seen[before] if isinstance(m, AIMessage) and m.content.startswith('report-')]
        return dict(agent_id=identity, child_thread_id=record['child_thread_id'], checkpoint_id=record['checkpoint_id'],
            delegation_id=record['last_delegation_id'], model_calls=len(child.seen) - before,
            budget_count=state.values['thread_model_call_count'], previous_reports=previous,
            report=envelope['report'], file_content=(workspace / filename).read_text(encoding='utf-8'), write_calls=written.call_count)

    async def query(app, sid, identity):
        before = len(child.seen)
        parent.replies.extend([call('subagent_list', {}, 'list-' + str(len(parent.seen))), AIMessage(content='found')])
        result = await app.arun(sid, [('user', 'Find the existing worker.')])
        feedback = tool_result(result, 'subagent_list')
        assert feedback.status == 'success', feedback.content
        item, = json.loads(feedback.content)['items']
        assert item['agent_id'] == identity and item['resume_reason'] is None
        return dict(agent_id=item['agent_id'], resume_available=item['resume_available'],
            child_model_calls=len(child.seen) - before, bytes=len(feedback.content.encode('utf-8')))

    write_module = importlib.import_module('naiyue_test_agent.tools.write')
    with patch.object(write_module, '_write', wraps=write_module._write) as written:
        with patch.object(AutoPermissionReviewer, 'areview', side_effect=AssertionError('Unexpected Auto call')) as auto:
            async with opened() as app:
                sid = await app.acreate_workspace_session('root', workspace)
                first = await delegate(app, sid, 1)
                identity = first['agent_id']
                second = await delegate(app, sid, 2, identity)
                lists = [await query(app, sid, identity)]
                saved = await app.aget_subagent_history(sid, identity)
                ids = [message.id for message in saved['messages']]
            before = len(parent.seen) + len(child.seen), written.call_count, auto.call_count
            async with opened() as app:
                await app.arestore_session(sid)
                history = await app.aget_subagent_history(sid, identity)
                owner = app._sessions.require(sid)
                reopen = dict(model_calls=len(parent.seen) + len(child.seen) - before[0],
                    write_calls=written.call_count - before[1], auto_calls=auto.call_count - before[2],
                    drivers=len(owner._run.subagent_instances) if owner._run else 0,
                    approvals=len(app.pending_approval_batches(sid)),
                    history_ids_equal=[message.id for message in history['messages']] == ids)
                lists.append(await query(app, sid, identity))
                third = await delegate(app, sid, 3, identity)
                fresh = await delegate(app, sid, 'fresh')
                final = await app.aget_subagent_history(sid, identity)
                assert [m.id for m in final['messages']][:len(ids)] == ids
                assert [m.content for m in final['messages'] if isinstance(m, AIMessage) and m.content.startswith('report-')] == [
                    'report-1', 'report-2', 'report-3']
                assert all((workspace / f'effect-{i}.txt').read_text(encoding='utf-8') == f'effect-{i}' for i in (1, 2, 3))
                assert app.get_active_tools(sid) == ('subagent',)
            report = dict(continuations=[first, second, third], fresh=fresh, lists=lists, reopen=reopen,
                root_model_calls=len(parent.seen), child_model_calls=len(child.seen), write_calls=written.call_count,
                provider_requests=0)
            assert not parent.replies and not child.replies
            verify_subagent_report(report)
            return report
