"""Default Web launch is a scoped service client, never a database owner."""
import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from naiyue_test_agent.web import __main__ as entry
from naiyue_test_agent.service.settings import ServiceError
from test_service_protocol import service_clients, command
from test_async_application import StreamingToolModel


class Client:
    def __init__(self, prepared=None):
        self._record = SimpleNamespace(origin='http://127.0.0.1:8123', access_token='x'*40)
        self.instance_id = 'a'*32
        self.prepared = prepared or {'status': 'ready', 'scope_id': 'b'*32, 'root': '/project'}
        self.calls, self.closed = [], False

    async def request(self, method, path, **options):
        self.calls.append((method, path, options))
        return self.prepared

    async def aclose(self):
        self.closed = True


def setup(monkeypatch, tmp_path, client):
    (tmp_path / 'index.html').write_text('<html>fixture</html>')
    seen = []
    async def connect(options):
        seen.append(options)
        return client
    monkeypatch.setattr(entry, 'ensure_service', connect)
    return seen


def test_launcher_returns_scoped_link_and_only_closes_client(monkeypatch, tmp_path, capsys):
    client = Client()
    seen = setup(monkeypatch, tmp_path, client)
    assert entry.main(['--workspace', str(tmp_path), '--frontend-dist', str(tmp_path), '--trust-project']) == 0
    link = next(line.split('：', 1)[1] for line in capsys.readouterr().out.splitlines() if '本地访问链接：' in line)
    parts = urlsplit(link)
    assert not parts.query
    assert parse_qs(parts.fragment) == {'access_token': ['x'*40], 'scope_id': ['b'*32], 'instance_id': ['a'*32]}
    assert client.closed and len(seen) == 1
    assert [path for _, path, _ in client.calls] == ['/api/v1/terminal/prepare']
    assert client.calls[0][2]['json']['trusted'] is True


@pytest.mark.parametrize('answer,expected,calls', [('', 0, 1), ('trust', 0, 2)])
def test_untrusted_project_needs_explicit_confirmation(monkeypatch, tmp_path, answer, expected, calls):
    client = Client({'status': 'trust_required', 'root': str(tmp_path)})
    setup(monkeypatch, tmp_path, client)
    monkeypatch.setattr(entry.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _: answer)
    original = client.request
    async def request(*args, **kwargs):
        value = await original(*args, **kwargs)
        return {'status': 'ready', 'scope_id': 'b'*32, 'root': str(tmp_path)} if len(client.calls) == 2 else value
    client.request = request
    assert entry.main(['--frontend-dist', str(tmp_path)]) == expected
    assert len(client.calls) == calls and client.closed


def test_noninteractive_untrusted_launch_does_not_register(monkeypatch, tmp_path, capsys):
    client = Client({'status': 'trust_required', 'root': str(tmp_path)})
    setup(monkeypatch, tmp_path, client)
    monkeypatch.setattr(entry.sys.stdin, 'isatty', lambda: False)
    assert entry.main(['--frontend-dist', str(tmp_path)]) == 1
    assert len(client.calls) == 1 and client.closed
    assert 'project_trust_required' in capsys.readouterr().out


def test_service_hosts_public_assets_but_not_unauthorized_api(monkeypatch, tmp_path):
    async def run():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, _, app, _):
            response = await a.get('/', headers={'Authorization': ''})
            assert response.status_code == 200 and '<html>fixture</html>' == response.text
            assert 'access_token' not in response.text
            assert response.headers['referrer-policy'] == 'no-referrer'
            assert (await a.get('/api/v1/scopes/chats/sessions', headers={'Authorization': ''})).status_code == 401
            assert (await a.get('/api/sessions')).status_code == 404
            assert not app.state.scopes.services
    asyncio.run(run())


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_instance_mismatch_rejected_before_host_or_receipt(monkeypatch, tmp_path, method):
    async def run():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, _, app, _):
            response = await a.request(method, '/api/v1/scopes/chats/sessions',
                json={'model_id': 'A'} if method == 'POST' else None,
                headers={**command(), 'X-Service-Instance': 'f'*32})
            assert response.status_code == 409
            assert response.json()['error']['code'] == 'service_instance_mismatch'
            assert not app.state.scopes.services and not app.state.receipts.entries
    asyncio.run(run())


def test_trust_prompt_escapes_terminal_control_characters(monkeypatch, tmp_path, capsys):
    root = tmp_path / 'untrusted\x1b[2K\r项目目录：safe'
    root.mkdir()
    client = Client({'status': 'trust_required', 'root': str(root)})
    setup(monkeypatch, tmp_path, client)
    monkeypatch.setattr(entry.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _: '')
    assert entry.main(['--workspace', str(root), '--frontend-dist', str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert '\x1b' not in output and '\r' not in output
    assert '\\u001b[2K\\r' in output
