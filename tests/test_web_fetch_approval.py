"""An actual local HTTP server is contacted only after real graph approval."""

import asyncio

from langchain_core.messages import AIMessage, ToolMessage


def test_private_http_approval_and_deny_on_redirect(monkeypatch, tmp_path):
    from naiyue_test_agent.application import async_application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools.web_fetch import create_web_fetch_tool
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    from test_async_application import StreamingToolModel, call
    async def scenario():
        requests = []
        async def serve(reader, writer):
            try:
                head = await reader.readuntil(b'\r\n\r\n')
                requests.append(head.split(b'\r\n')[0])
                if b'/redirect ' in head:
                    writer.write(b'HTTP/1.1 302 Found\r\nLocation: /denied\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                else:
                    body = b'<h1>actual local response</h1>'
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: '
                                 + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(serve, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        model = StreamingToolModel(replies=[call('web_fetch', {'url': base + '/docs'}, 'first'),
            AIMessage(content='read done'), call('web_fetch', {'url': base + '/redirect'}, 'second'),
            AIMessage(content='redirect handled')])
        settings = AppSettings.model_validate({'permissions': {'deny': [
            {'tool': 'web_fetch', 'args': {'url': base + '/denied'}}]}})
        # The builder obtains the catalog through application package exports.
        from naiyue_test_agent import application
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        app = await async_application.build_async_local_application('synthetic',
            tools=[create_web_fetch_tool(network_settings=NetworkSettings(mode='direct'))],
            settings=settings, system_prompt='', response_format=None)
        try:
            sid = await app.acreate_workspace_session('A', tmp_path)
            pending = await app.arun(sid, [('user', 'read local documentation')])
            assert pending['approval_requests'] and requests == []
            result = await app.aresume_tool_approval(sid, ['approve'])
            feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert feedback.status == 'success' and 'actual local response' in feedback.content
            assert len(requests) == 1
            pending = await app.arun(sid, [('user', 'read redirect')])
            assert pending['approval_requests'] and len(requests) == 1
            result = await app.aresume_tool_approval(sid, ['approve'])
            feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage) and m.tool_call_id == 'second')
            assert feedback.status == 'error' and 'denied or needs separate approval' in feedback.content
            assert len(requests) == 2 and all(b'/denied ' not in line for line in requests)
        finally:
            await app.aclose()
            server.close()
            await server.wait_closed()
    asyncio.run(scenario())
