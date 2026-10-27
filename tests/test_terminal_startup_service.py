"""Terminal preflight belongs to the service and precedes model construction."""
import asyncio
import httpx
from naiyue_test_agent import application
from naiyue_test_agent.service.app import create_service_app
from naiyue_test_agent.service.ownership import read_discovery
from test_service_ownership import options
from test_service_protocol import command


def test_empty_history_needs_no_models_or_business_database(monkeypatch, tmp_path):
    def forbidden(*args):
        raise AssertionError('history selection must not construct models')
    monkeypatch.setattr(application, 'load_model_catalog', forbidden)
    async def scenario():
        config = options(tmp_path)
        app = create_service_app(config)
        async with app.router.lifespan_context(app):
            record = read_discovery(config)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=record.origin,
                    headers={'Authorization': f'Bearer {record.access_token}', 'Origin': record.origin}) as client:
                response = await client.post('/api/v1/terminal/prepare', json={'root': str(tmp_path),
                    'database_path': str(tmp_path / 'absent.sqlite'), 'resume': True}, headers=command())
                assert response.status_code == 200 and response.json()['status'] == 'empty'
                assert not list(tmp_path.rglob('*.sqlite*'))
    asyncio.run(scenario())


def test_cli_untrusted_noninteractive_and_cancel_do_not_register_or_load_models(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace
    from naiyue_test_agent.cli import chat, chat_startup
    from terminal_service_helpers import TerminalService
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: (_ for _ in ()).throw(AssertionError('No model')))
    root = tmp_path / 'untrusted'
    root.mkdir()
    with TerminalService(options(tmp_path)) as server:
        monkeypatch.setattr(chat_startup, 'TerminalClient', lambda _: server.client())
        monkeypatch.setattr(chat.sys, 'stdin', SimpleNamespace(isatty=lambda: False))
        assert chat.main(['--workspace', str(root)]) == 1
        assert 'trust-project' in capsys.readouterr().out
        monkeypatch.setattr(chat.sys, 'stdin', SimpleNamespace(isatty=lambda: True))
        monkeypatch.setattr('builtins.input', lambda _: '')
        assert chat.main(['--workspace', str(root)]) == 0
        client = server.client()
        try:
            assert client.request('GET', '/api/v1/projects')['items'] == []
        finally:
            client.close()
        assert not (root / '.naiyue/workspace.json').exists()
        assert not list(server.options.data_dir.rglob('sessions.sqlite'))
