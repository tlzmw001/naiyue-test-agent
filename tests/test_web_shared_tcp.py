"""Two default launchers, browser clients and a terminal share one real TCP owner."""
import asyncio
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit

import httpx

from naiyue_test_agent.cli.terminal_client import TerminalClient
from naiyue_test_agent.service.client import ServiceClient
from naiyue_test_agent.web import __main__ as entry
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel
from test_service_tcp import tcp_service
from test_web_api import command_headers


def test_two_default_launchers_and_terminal_share_live_run(monkeypatch, tmp_path, capsys):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[], entered=entered, release=release)
        async with tcp_service(monkeypatch, tmp_path, model) as (app, options, server, _):
            (options.frontend_dist / 'index.html').write_text('<html>shared</html>')
            root = tmp_path / 'project'
            root.mkdir()
            async def connect(requested):
                assert requested.identity == options.identity
                return await ServiceClient.connect(options)
            monkeypatch.setattr(entry, 'ensure_service', connect)
            monkeypatch.setattr(entry, 'load_settings', lambda path: AppSettings.model_validate(
                {'storage': {'data_dir': options.data_dir}}))
            arguments = ['--workspace', str(root), '--config', str(options.config_path),
                '--settings', str(options.settings_path), '--frontend-dist', str(options.frontend_dist)]
            assert await asyncio.to_thread(entry.main, [*arguments, '--trust-project']) == 0
            assert await asyncio.to_thread(entry.main, arguments) == 0
            links = [line.split('：', 1)[1] for line in capsys.readouterr().out.splitlines() if line.startswith('本地访问链接：')]
            assert len(links) == 2
            a, b = [parse_qs(urlsplit(link).fragment) for link in links]
            assert a == b and a['instance_id'] == [app.state.owner.record.instance_id]
            scope = a['scope_id'][0]
            base = f'/api/v1/scopes/{scope}'
            headers = {'Authorization': 'Bearer ' + a['access_token'][0],
                'Origin': app.state.owner.record.origin, 'X-Service-Instance': a['instance_id'][0]}
            async with httpx.AsyncClient(base_url=app.state.owner.record.origin, headers=headers,
                    event_hooks={'request': [command_headers]}) as browser:
                sid = (await browser.post(base + '/sessions', json={'model_id': 'A'})).json()['session_id']
                started = await browser.post(base + f'/sessions/{sid}/runs', json={'text': 'wait', 'request_id': str(uuid4())})
                rid = started.json()['run_id']
                await asyncio.wait_for(entered.wait(), 5)
                async with browser.stream('GET', base + f'/runs/{rid}/events') as stream:
                    async for line in stream.aiter_lines():
                        if line.startswith('data:'): break
                assert (await browser.get(base + f'/runs/{rid}')).json()['status'] == 'running'
                busy = await browser.post(base + f'/sessions/{sid}/runs', json={'text': 'overlap', 'request_id': str(uuid4())})
                assert busy.status_code == 409 and busy.json()['error']['code'] == 'session_busy'
                def terminal():
                    client = TerminalClient(options)
                    try:
                        client.bind(scope)
                        assert client.active_run(sid) == rid
                        assert client._http.instance_id == a['instance_id'][0]
                        client.control(sid, 'stop')
                        assert client.wait(sid, rid)['run']['status'] == 'stopped'
                    finally:
                        client.close()
                await asyncio.to_thread(terminal)
                assert (await browser.get(base + f'/runs/{rid}')).json()['status'] == 'stopped'
                assert (await browser.get(base + f'/sessions/{sid}')).json()['busy'] is False
                assert not release.is_set(), 'No test release should be needed to stop from another client'
                assert not server.should_exit
                assert len(app.state.scopes.services) == 1
                assert len(model.seen) == 1
    asyncio.run(run())
