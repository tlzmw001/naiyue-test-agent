"""Pinned online SDK retry waits are cancellable without a second HTTP attempt."""

import asyncio

import httpx2
import pytest

from naiyue_test_agent.agent_runtime.run_control import RunControl, UserStop


@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
def test_user_stop_cancels_actual_sdk_retry_backoff(monkeypatch, provider):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []

        def respond(request):
            requests.append(request)
            return httpx2.Response(429, json={'error': {'type': 'rate_limit_error', 'message': 'synthetic'}},
                                   headers={'retry-after': '30'})

        async def backoff(_seconds):
            entered.set()
            await original_sleep(_seconds)

        if provider == 'openai':
            import openai
            sdk = openai.AsyncOpenAI
            original_sleep = openai._base_client.anyio.sleep
            monkeypatch.setattr(openai._base_client.anyio, 'sleep', backoff)
        else:
            import anthropic
            sdk = anthropic.AsyncAnthropic
            original_sleep = anthropic._base_client.anyio.sleep
            monkeypatch.setattr(anthropic._base_client.anyio, 'sleep', backoff)

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as transport:
            async with sdk(api_key='synthetic-test-key', http_client=transport, max_retries=2) as client:
                async def request():
                    if provider == 'openai':
                        return await client.responses.create(model='synthetic', input='probe')
                    return await client.messages.create(model='synthetic', max_tokens=8,
                        messages=[{'role': 'user', 'content': 'probe'}])

                control = RunControl()
                task = asyncio.create_task(control.await_model(request))
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    control.request()
                    with pytest.raises(UserStop):
                        await asyncio.wait_for(task, 5)
                    assert len(requests) == 1
                finally:
                    release.set()
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
