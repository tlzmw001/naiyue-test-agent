"""Final live windows must not skip archived messages which they displace."""

import asyncio

from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel
from test_web_api import web_client, request


def test_root_window_cursor_covers_archive_displaced_by_a_live_draft(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(id=f'answer-{i}', content=f'answer {i}') for i in range(5)])
        async with web_client(monkeypatch, tmp_path, model, history_page_messages=2) as (client, web):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            for i in range(4):
                response = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request(f'question {i}'))
                await web.state.delivery.wait(response.json()['run_id'])
            saved = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/messages')).json()['messages']
            model.token_entered, model.token_release = asyncio.Event(), asyncio.Event()
            response = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('new question'))
            rid = response.json()['run_id']
            try:
                await asyncio.wait_for(model.token_entered.wait(), 5)
                window = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
                assert not {m['id'] for m in saved} & {m['id'] for m in window['messages']}
                page = await client.get(f'/api/v1/scopes/chats/sessions/{sid}/messages', params={'before': window['history']['next_cursor']})
                assert [m['id'] for m in page.json()['messages']] == [m['id'] for m in saved]
            finally:
                model.token_release.set()
                await web.state.delivery.wait(rid)
    asyncio.run(scenario())


def test_child_window_cursor_covers_archive_displaced_by_later_tool_results(monkeypatch, tmp_path):
    from naiyue_test_agent.settings import AppSettings
    from test_async_application import call
    from test_subagent_execution import ChildModel, role
    async def scenario():
        root = tmp_path / 'work'
        root.mkdir()
        (root / 'sample').write_text('saved child content')
        first, second, release_first, release_second = (asyncio.Event() for _ in range(4))
        async def gate(messages):
            identity = getattr(messages[-1], 'tool_call_id', '')
            if identity == 'inner-4': first.set(); await release_first.wait()
            if identity == 'inner-9': second.set(); await release_second.wait()
        model = ChildModel(replies=[call('subagent', {'agent': 'tester', 'task': 'reads'}, 'outer'),
            *(call('read', {'path': 'sample'}, f'inner-{i}') for i in range(10)),
            AIMessage(content='child done'), AIMessage(content='root done')], on_request=gate)
        runtime = AppSettings(subagents=(role(tool_names=('read',), model_id=None),))
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root, runtime_settings=runtime,
                              history_page_messages=2) as (client, web):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            response = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('delegate'))
            rid = response.json()['run_id']
            try:
                await asyncio.wait_for(first.wait(), 5)
                assert (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/prepare')).status_code == 200
                child, = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents')).json()
                agent = child['agent_id']
                saved = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/messages', params={'agent_id': agent})).json()['messages']
                release_first.set()
                await asyncio.wait_for(second.wait(), 5)
                window = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents/{agent}')).json()
                assert not {m['id'] for m in saved} & {m['id'] for m in window['messages']}
                page = await client.get(f'/api/v1/scopes/chats/sessions/{sid}/messages', params={'agent_id': agent, 'before': window['next_cursor']})
                assert [m['id'] for m in page.json()['messages']] == [m['id'] for m in saved]
            finally:
                release_first.set(); release_second.set()
                await web.state.delivery.wait(rid)
    asyncio.run(scenario())
