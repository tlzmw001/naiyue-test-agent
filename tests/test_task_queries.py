"""Application-owned task queries combine owners without weakening their scope."""

from dataclasses import replace
import asyncio
import hashlib
import json

import pytest
from langchain_core.tools import ToolException

from naiyue_test_agent.application.sessions.background_tasks import TaskQueryError


def row(number, *, kind=None, session='s', root='/workspace', status='running'):
    item = dict(task_id=f'{number:032x}', session_id=session, workspace_root=root,
        created_at=f'2026-10-22T10:{number // 60:02}:{number % 60:02}+00:00', status=status)
    if kind:
        item['kind'] = kind
    return item


class Owner:
    def __init__(self, rows, terminal=0):
        self.rows, self.terminal, self.calls = rows, terminal, []

    async def list(self, **scope):
        self.calls.append(('list', scope))
        selected = [item for item in self.rows if
            (scope.get('session_id') is None or item['session_id'] == scope['session_id']) and
            (scope.get('workspace_root') is None or item['workspace_root'] == str(scope['workspace_root']))]
        start = 0
        if scope.get('before') is not None:
            found = [index for index, item in enumerate(selected) if item['task_id'] == scope['before']]
            if not found:
                raise TaskQueryError('task_cursor_invalid', 'Invalid cursor')
            start = found[0] + 1
        limit = scope.get('limit', 20)
        page = selected[start:start + limit]
        return dict(items=page, next_before=page[-1]['task_id'] if start + limit < len(selected) else None,
            active_count=sum(item['status'] in ('starting', 'running', 'stopping') for item in selected),
            terminal_revision=self.terminal,
            revision=hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest())

    async def output(self, task_id, **scope):
        self.calls.append(('output', task_id, scope))
        rows = (await self.list(**scope, limit=100))['items']
        for item in rows:
            if item['task_id'] == task_id:
                return dict(item, output='summary')
        raise TaskQueryError('task_not_found', 'Task outside scope')

    async def stop(self, task_id, **scope):
        self.calls.append(('stop', task_id, scope))
        return dict(await self.output(task_id, **scope), status='finished')


def test_shell_only_delegates_exact_arguments_and_result():
    from naiyue_test_agent.application.sessions.task_queries import TaskQueries
    async def scenario():
        shell = Owner([row(2), row(1)], terminal=3)
        queries = TaskQueries(shell)
        expected = await shell.list(session_id='s', limit=1, before=None, workspace_root=None)
        actual = await queries.list(session_id='s', limit=1)
        assert actual == expected
        assert shell.calls[-1] == ('list', dict(session_id='s', workspace_root=None, limit=1, before=None))
        assert (await queries.output(row(2)['task_id'], session_id='s'))['output'] == 'summary'
        assert (await queries.stop(row(2)['task_id'], session_id='s'))['status'] == 'finished'
    asyncio.run(scenario())


def test_combined_list_paginates_before_ids_and_scopes_before_merging(tmp_path):
    from naiyue_test_agent.application.sessions.task_queries import TaskQueries
    async def scenario():
        root = str(tmp_path)
        shell = Owner([row(7, session='other', root=root), row(5, root=root), row(1, root=root)], terminal=2)
        memory = Owner([row(6, kind='memory', root='/elsewhere'), row(4, kind='memory', root=root,
            status='finished'), row(3, kind='memory', root=root)], terminal=3)
        queries = TaskQueries(shell, memory)
        first = await queries.list(session_id='s', workspace_root=tmp_path, limit=2)
        second = await queries.list(session_id='s', workspace_root=tmp_path, limit=2, before=first['next_before'])
        assert [item['task_id'] for item in first['items'] + second['items']] == [row(i)['task_id'] for i in (5, 4, 3, 1)]
        assert first['next_before'] == row(4)['task_id'] and second['next_before'] is None
        assert first['active_count'] == 3 and first['terminal_revision'] == 5
        assert second['revision'] == first['revision']
        with pytest.raises(TaskQueryError) as error:
            await queries.list(session_id='s', workspace_root=tmp_path, before=row(7)['task_id'])
        assert error.value.code == 'task_cursor_invalid'
        for operation in (queries.output, queries.stop):
            assert (await operation(row(3)['task_id'], session_id='s', workspace_root=tmp_path))['kind'] == 'memory'
            with pytest.raises(TaskQueryError) as error:
                await operation(row(7)['task_id'], session_id='s', workspace_root=tmp_path)
            assert error.value.code == 'task_not_found'
    asyncio.run(scenario())


def test_combined_list_includes_older_backend_pages_and_revisions():
    from naiyue_test_agent.application.sessions.task_queries import TaskQueries
    async def scenario():
        shell = Owner([row(i) for i in range(150, 0, -1)])
        memory = Owner([row(151, kind='memory')])
        queries = TaskQueries(shell, memory)
        first = await queries.list(limit=100)
        second = await queries.list(limit=100, before=first['next_before'])
        assert len(first['items']) == 100 and len(second['items']) == 51
        assert len({item['task_id'] for item in first['items'] + second['items']}) == 151
        before = first['revision']
        memory.rows[0]['status'] = 'finished'
        assert (await queries.list())['revision'] != before
        for limit in (False, 0, 101, 1.5, '20'):
            with pytest.raises(ToolException):
                await queries.list(limit=limit)
    asyncio.run(scenario())


def test_combined_list_rejects_owner_revision_changing_between_pages():
    from naiyue_test_agent.application.sessions.task_queries import TaskQueries
    async def scenario():
        shell = Owner([row(i) for i in range(150, 0, -1)])
        original = shell.list
        async def changed(**scope):
            page = await original(**scope)
            if scope.get('before') is not None:
                page['revision'] = 'concurrently changed'
            return page
        shell.list = changed
        with pytest.raises(TaskQueryError) as error:
            await TaskQueries(shell, Owner([])).list()
        assert error.value.code == 'task_cursor_invalid'
    asyncio.run(scenario())


@pytest.mark.parametrize('action', ['output', 'stop'])
@pytest.mark.parametrize('failure', [OSError('private IO failure'), TaskQueryError('cleanup_unconfirmed', 'still active')])
def test_owner_failures_never_turn_into_missing_task(action, failure):
    from naiyue_test_agent.application.sessions.task_queries import TaskQueries
    async def scenario():
        shell, memory = Owner([]), Owner([row(1, kind='memory')])
        async def broken(*args, **kwargs):
            raise failure
        setattr(shell, action, broken)
        with pytest.raises(type(failure)) as caught:
            await getattr(TaskQueries(shell, memory), action)(row(1)['task_id'], session_id='s')
        assert caught.value is failure and memory.calls == []
    asyncio.run(scenario())


@pytest.mark.parametrize('name,args', [('task_list', {'limit': 20, 'before': None}),
    ('task_output', {'task_id': '1'.zfill(32)}), ('task_stop', {'task_id': '1'.zfill(32)})])
def test_query_tools_use_trusted_query_port_and_preserve_permit_binding(tmp_path, name, args):
    from naiyue_test_agent.tools.background_tasks import create_background_task_tools
    from naiyue_test_agent.tools.permissions import PermissionMechanismError
    from test_shell_task_settings import runtime_for
    async def scenario():
        spec = next(item for item in create_background_task_tools() if item.tool.name == name)
        runtime = runtime_for(spec, args, tmp_path)
        owner = Owner([row(1, root=str(tmp_path), kind='memory')])
        runtime.context = replace(runtime.context, scope=replace(runtime.context.scope, capabilities=replace(runtime.context.scope.capabilities, task_queries=owner)))
        result = json.loads(await spec.tool.coroutine(**args, runtime=runtime))
        assert result['items'][0]['kind'] == 'memory' if name == 'task_list' else result['kind'] == 'memory'
        expected = name.removeprefix('task_')
        assert owner.calls[0][0] == expected
        assert owner.calls[0][-1]['session_id'] == 's' and owner.calls[0][-1]['workspace_root'] == tmp_path
        runtime.context.authorization.permit['subject']['args'] = {}
        with pytest.raises(PermissionMechanismError):
            await spec.tool.coroutine(**args, runtime=runtime)
        runtime = runtime_for(spec, args, tmp_path)
        runtime.context = replace(runtime.context, scope=replace(runtime.context.scope, capabilities=replace(runtime.context.scope.capabilities, process_tasks=owner)))
        with pytest.raises(ToolException, match='scope'):
            await spec.tool.coroutine(**args, runtime=runtime)
        runtime = runtime_for(spec, args, tmp_path)
        runtime.context = replace(runtime.context, scope=replace(runtime.context.scope, capabilities=replace(runtime.context.scope.capabilities, task_queries=owner), identity=replace(runtime.context.scope.identity, agent_path='child')))
        runtime.context.authorization.permit['agent_path'] = 'child'
        before = len(owner.calls)
        with pytest.raises(ToolException, match='root'):
            await spec.tool.coroutine(**args, runtime=runtime)
        assert len(owner.calls) == before
    asyncio.run(scenario())
