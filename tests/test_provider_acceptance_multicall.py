"""SDK diagnostic requests stay guarded and disclose shapes, never argument values."""

import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from langchain_openai import ChatOpenAI

from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_subagent_tool
from scripts.provider_acceptance_budget import RequestBudget, guard_catalog
from scripts.provider_acceptance_cases import CaseEnvironment, AcceptanceCheckFailed
from scripts.provider_acceptance_multicall import multicall_diagnostic, nonstream_tool_diagnostic


@pytest.mark.parametrize('variant', ['complete_stream', 'missing_deltas', 'nonstream'])
def test_diagnostic_sdk_transport_and_incomplete_gateway_shape(monkeypatch, tmp_path, variant):
    attempts = []
    items = [{'id': f'fc_{i}', 'type': 'function_call', 'name': 'subagent', 'call_id': f'call_{i}',
        'status': 'completed', 'arguments': json.dumps({'agent': 'tester', 'task': task})}
        for i, task in enumerate(('LEFT', 'RIGHT'))]
    payload = {'id': 'resp_synthetic', 'object': 'response', 'created_at': 1, 'status': 'completed',
               'model': 'synthetic', 'error': None, 'incomplete_details': None, 'output': items}

    async def transport(_, request):
        attempts.append(json.loads(request.content).get('stream', False))
        response = httpx.Response if isinstance(request, httpx.Request) else httpx2.Response
        if variant == 'nonstream':
            return response(200, json=payload)
        events = []
        for i, item in enumerate(items):
            events.append({'type': 'response.output_item.added', 'output_index': i, 'item': {**item, 'arguments': ''}})
            if variant == 'complete_stream':
                events.append({'type': 'response.function_call_arguments.delta', 'output_index': i, 'delta': item['arguments']})
            events.append({'type': 'response.output_item.done', 'output_index': i, 'item': item})
        body = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events)
        return response(200, headers={'content-type': 'text/event-stream'}, content=body.encode())
    for module in (httpx, httpx2):
        monkeypatch.setattr(module.AsyncHTTPTransport, 'handle_async_request', transport)

    async def scenario():
        budget = RequestBudget(1)
        model = ChatOpenAI(model='synthetic', api_key='synthetic', base_url='https://example.invalid/v1', use_responses_api=True)
        catalog = ModelCatalog('synthetic', {'synthetic': model})
        guard_catalog(catalog, budget)
        env = CaseEnvironment('unused', 'synthetic', tmp_path, budget)
        @asynccontextmanager
        async def opened(*args):
            try:
                yield SimpleNamespace(_catalog=catalog, get_registered_tools=lambda: [create_subagent_tool()])
            finally:
                await catalog.aclose()
        env.open_app = opened
        execute = nonstream_tool_diagnostic if variant == 'nonstream' else multicall_diagnostic
        if variant == 'missing_deltas':
            with pytest.raises(AcceptanceCheckFailed, match='diagnostic_lc_matches_native'):
                await execute(env)
            assert all(row['keys'] == [] for row in env.diagnostics['arguments'])
        else:
            result = await execute(env)
            assert result['expected_arguments'] is True
        assert budget.snapshot()['used'] == 1 and len(attempts) == 1
        assert attempts == [variant != 'nonstream']
        assert 'LEFT' not in json.dumps(env.evidence()) and 'example.invalid' not in json.dumps(env.evidence())
    asyncio.run(scenario())
