"""Installed project listing is an authenticated service read and needs no models."""
from uuid import uuid4

from naiyue_test_agent import application
from naiyue_test_agent.cli import main
from test_service_ownership import options
from terminal_service_helpers import TerminalService


def test_projects_command_lists_registered_project_without_loading_models(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.cli import projects
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: (_ for _ in ()).throw(AssertionError('No model')))
    root = tmp_path / 'folder'
    root.mkdir()
    with TerminalService(options(tmp_path)) as server:
        client = server.client()
        try:
            added = client.request('POST', '/api/v1/projects/local',
                json={'root': str(root), 'trusted': True}, request_id=str(uuid4()))
        finally:
            client.close()
        before = {p: p.read_bytes() for p in server.options.data_dir.rglob('sessions.sqlite')}
        monkeypatch.setattr(projects, 'TerminalClient', lambda _: server.client())
        assert main(['projects']) == 0
        output = capsys.readouterr().out
        assert added['project_id'] in output and str(root) in output and 'folder' in output
        assert {p: p.read_bytes() for p in server.options.data_dir.rglob('sessions.sqlite')} == before
        assert server.inspect(lambda app: app._lifetime._children) == []
