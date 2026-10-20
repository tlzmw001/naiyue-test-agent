"""MCP-00 equal-condition experiments. Never product feature acceptance."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clients import PROJECT, owned, http_fixture, read_audit, alive, until
from fixture import SCHEMA
from boundary import SchemaSpec, tool_boundary, prepared_input
sys.path.insert(0, str(PROJECT / 'tests'))
from native_application_helpers import async_test
import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException
from naiyue_test_agent.tools.permissions import OneUseApprovalGrants, permission_binding, PermissionMechanismError

VALID = {'selection': {'kind': 'count', 'value': 3}}


@asynccontextmanager
async def connected(route, transport, tmp_path):
    if transport == 'http':
        async with http_fixture(tmp_path / 'http.jsonl') as url:
            async with owned(route, tmp_path / 'client.jsonl', url) as owner:
                yield owner, tmp_path / 'http.jsonl'
    else:
        async with owned(route, tmp_path / 'stdio.jsonl') as owner:
            yield owner, tmp_path / 'stdio.jsonl'
        pids = [x['pid'] for x in read_audit(tmp_path / 'stdio.jsonl') if x['event'] == 'start']
        await until(lambda: all(not alive(pid) for pid in pids))
        assert len(pids) == 1


def calls(path, name=None):
    return [x for x in read_audit(path) if x['event'] == 'call' and (name is None or x['tool'] == name)]


@pytest.mark.parametrize('route', ['sdk', 'lc'])
@pytest.mark.parametrize('transport', ['stdio', 'http'])
@pytest.mark.parametrize('case', ['approve', 'deny', 'invalid-type', 'invalid-extra', 'label', 'grant-reuse'])
@async_test
async def test_generic_schema_permission_and_runtime(route, transport, case, tmp_path):
    async with connected(route, transport, tmp_path) as (owner, audit):
        schema = owner.candidate.schemas['echo']
        assert schema == SCHEMA
        spec, settings, graph = tool_boundary(owner, schema)
        args = {'selection': {'kind': 'label', 'value': 'B'}} if case == 'label' else dict(VALID)
        if case == 'invalid-type':
            args = {'selection': {'kind': 'count', 'value': '3'}}
        if case == 'invalid-extra':
            args = {**args, 'runtime': {'session_id': 'spoof'}}
        context = {'session_id': 'research-A', 'agent_path': 'root'}
        state, prepared = prepared_input(spec, settings, args, context)
        assert calls(audit) == []
        if not case.startswith('invalid'):
            assert prepared['subject']['args'] == args
            assert 'note' not in prepared['subject']['args']
            if case == 'deny':
                prepared.update(decision='deny', source='config_deny')
            else:
                binding = permission_binding(context, 'batch-1', prepared)
                context['approval_grants'] = OneUseApprovalGrants({'outer-call': binding})
        result = await graph.ainvoke(state, context=context)
        message = result['messages'][-1]
        assert isinstance(message, ToolMessage) and message.tool_call_id == 'outer-call'
        expected = 0 if case.startswith('invalid') or case == 'deny' else 1
        assert len(calls(audit)) == expected
        assert message.status == ('success' if expected else 'error')
        if expected:
            assert calls(audit)[0]['args'] == args
            assert json.loads(message.content)['args'] == args
        if case == 'grant-reuse':
            with pytest.raises(PermissionMechanismError, match='One-use approval'):
                await graph.ainvoke(state, context=context)
            assert len(calls(audit)) == 1


@pytest.mark.parametrize('route', ['sdk', 'lc'])
@pytest.mark.parametrize('transport', ['stdio', 'http'])
@async_test
async def test_owner_cross_tasks_parallel_cancel_and_explicit_close(route, transport, tmp_path):
    async with connected(route, transport, tmp_path) as (owner, audit):
        first = await asyncio.create_task(owner.call('echo', VALID))
        results = await asyncio.gather(*(owner.call('echo', VALID) for _ in range(4)))
        assert all(r == first for r in results)
        assert owner.candidate.enter_task is owner.task
        assert owner.task is not asyncio.current_task()
        waiting = asyncio.create_task(owner.call('wait', {}))
        await until(lambda: len(calls(audit, 'wait')) == 1)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert await asyncio.create_task(owner.call('echo', VALID)) == first
        in_flight = asyncio.create_task(owner.call('wait', {}))
        await until(lambda: len(calls(audit, 'wait')) == 2)
        await asyncio.create_task(owner.close())
        assert in_flight.cancelled() and not owner.active and owner.task.done()
        assert owner.candidate.exit_task is owner.candidate.enter_task
        with pytest.raises(RuntimeError, match='owner_closing'):
            await owner.call('echo', VALID)


@pytest.mark.parametrize('route', ['sdk', 'lc'])
@pytest.mark.parametrize('transport', ['stdio', 'http'])
@pytest.mark.parametrize('case', ['error', 'interaction', 'budget', 'unknown-outcome'])
@async_test
async def test_failure_no_automatic_replay(route, transport, case, tmp_path):
    async with connected(route, transport, tmp_path) as (owner, audit):
        if case == 'error':
            with pytest.raises(ToolException, match='fixture_failure'):
                await owner.call('fail', {})
            name = 'fail'
        elif case == 'interaction':
            with pytest.raises(Exception) as failure:
                await owner.call('ask', {})
            assert ('unsupported_input_required' in str(failure.value) if route == 'sdk'
                    else type(failure.value).__name__ == 'InputRequiredRoundsExceededError')
            name = 'ask'
        elif case == 'budget':
            with pytest.raises(ValueError, match='result_budget_exceeded'):
                await owner.call('big', {})
            name = 'big'
        else:
            pending = asyncio.create_task(owner.call('lost', {}))
            await until(lambda: any(x['event'] == 'committed' for x in read_audit(audit)))
            # Event-synchronized timeout of a result that can no longer arrive.
            # 0 expires at next yield, not an unreliable 50ms timing assertion.
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0):
                    await pending
            name = 'lost'
        assert len(calls(audit, name)) == 1
        assert (await owner.call('echo', VALID))['structured']['args'] == VALID


@pytest.mark.parametrize('route', ['sdk', 'lc'])
@async_test
async def test_separate_stdio_owners_are_process_isolated(route, tmp_path):
    async with owned(route, tmp_path / 'A.jsonl') as a, owned(route, tmp_path / 'B.jsonl') as b:
        ra, rb = await asyncio.gather(a.call('echo', VALID), b.call('echo', VALID))
        assert ra['structured']['pid'] != rb['structured']['pid']
        assert await asyncio.create_task(a.call('echo', VALID)) == ra
    for file in ('A.jsonl', 'B.jsonl'):
        pid = read_audit(tmp_path / file)[0]['pid']
        await until(lambda: not alive(pid))


@pytest.mark.parametrize('bad_schema,reason', [
    ({'type': 'object', 'properties': {'runtime': {}}}, 'reserved_runtime'),
    ({'type': 'object', 'properties': {'x': {'$ref': 'https://invalid.example/schema'}}}, 'external_ref'),
])
def test_common_registration_rejects_unsupported_schema(bad_schema, reason):
    with pytest.raises(ValueError, match=reason):
        SchemaSpec(None, bad_schema)


@pytest.mark.parametrize('route', ['sdk', 'lc'])
@async_test
async def test_public_transport_json_budget_before_protocol_parse(route, tmp_path):
    from clients import Candidate, OwnedClient
    async with http_fixture(tmp_path / 'http.jsonl', json_response=True) as url:
        candidate = Candidate(route, tmp_path / 'client.jsonl', url, wire_budget=True)
        owner = OwnedClient(candidate)
        try:
            await owner.start()
            assert (await owner.call('echo', VALID))['structured']['args'] == VALID
            with pytest.raises(Exception, match='json_read_budget_exceeded') as failure:
                async with asyncio.timeout(15):
                    await owner.call('big', {})
            assert len(calls(tmp_path / 'http.jsonl', 'big')) == 1
            assert candidate.budget_facts
            assert all(f['rejected_bytes'] > 6144 and f['forwarded_bytes'] <= 6144
                       for f in candidate.budget_facts)
            assert failure.value is candidate.budget_facts[-1]['error']
        finally:
            await owner.close()
        assert owner.cleanup_errors  # Explicitly assert diagnosis after cleanup.


@pytest.mark.parametrize('route', ['sdk', 'lc'])
@async_test
async def test_current_application_loop_borrows_separate_session_owners(route, tmp_path, monkeypatch):
    from contextlib import AsyncExitStack
    from langchain.tools import tool, ToolRuntime
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from pydantic import BaseModel, ConfigDict
    from native_application_helpers import build_application
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import ToolSpec
    from naiyue_test_agent.tools.schemas import build_tool_schemas
    from naiyue_test_agent.tools.permissions import ToolPermission, require_tool_permit

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    class KnownInput(BaseModel):
        model_config = ConfigDict(strict=True, extra='forbid')
        selection: dict

    public, execution = build_tool_schemas('Mcp00KnownInput', KnownInput)
    owners = {}

    @tool('mcp00_echo', args_schema=execution)
    async def probe(selection: dict, runtime: ToolRuntime[dict, dict]) -> str:
        """Research current application loop and resource ownership."""
        require_tool_permit(runtime)
        owner = owners[runtime.context['session_id']]
        return json.dumps((await owner.call('echo', {'selection': selection}))['structured'])

    responses = []
    for identity in ('a1', 'b1', 'a2'):
        responses += [AIMessage(content='', tool_calls=[{'id': identity, 'name': 'mcp00_echo', 'args': VALID}]),
                      AIMessage(content='done')]
    model = Model(responses=responses)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    spec = ToolSpec(probe, public, permissions=ToolPermission(default='allow'))
    app = await build_application('research-only', tools=[spec], system_prompt='', response_format=None,
                                  settings=AppSettings())
    async with AsyncExitStack() as resources:
        a = await resources.enter_async_context(owned(route, tmp_path / 'A.jsonl'))
        b = await resources.enter_async_context(owned(route, tmp_path / 'B.jsonl'))
        sa, sb = await app.acreate_session('A'), await app.acreate_session('A')
        owners.update({sa: a, sb: b})
        graph = app._agent
        values = []
        for session, identity in ((sa, 'a1'), (sb, 'b1'), (sa, 'a2')):
            result = await asyncio.create_task(app.arun(session, [('user', 'research')]))
            message = next(m for m in result['messages'] if isinstance(m, ToolMessage)
                           and m.tool_call_id == identity)
            values.append(json.loads(message.content))
        assert values[0] == values[2] and values[0]['pid'] != values[1]['pid']
        assert app._agent is graph
        await asyncio.create_task(app.aclose())
        # Application has no MCP resource hook yet. Research stack supplies it
        # explicitly; never claim that app.aclose alone already closes MCP.
        await asyncio.create_task(resources.aclose())
    assert a.task.done() and b.task.done()
    await until(lambda: not alive(values[0]['pid']) and not alive(values[1]['pid']))


@pytest.mark.parametrize('same_failure', [True, False])
@async_test
async def test_owner_cleanup_preserves_original_and_propagates_new_failure(same_failure):
    from clients import OwnedClient
    original = OSError('original-read-failure')
    additional = OSError('different-cleanup-failure')

    class FaultyResource:
        async def __aenter__(self):
            return self

        async def call(self, name, args):
            raise original

        async def __aexit__(self, *exc):
            raise original if same_failure else additional

    owner = OwnedClient(FaultyResource())
    await owner.start()
    with pytest.raises(OSError) as failure:
        await owner.call('probe', {})
    assert failure.value is original
    if same_failure:
        await owner.close()
        assert owner.cleanup_errors == [original]
    else:
        with pytest.raises(OSError) as cleanup:
            await owner.close()
        assert cleanup.value is additional
        assert not owner.cleanup_errors
