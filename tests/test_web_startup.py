"""Ctrl-C after Uvicorn shutdown must not print a Python traceback to the local user."""

from naiyue_test_agent.web import __main__ as entry


def test_web_ctrl_c_returns_conventional_status_without_traceback(monkeypatch, tmp_path, capsys):
    (tmp_path / 'index.html').write_text('<html>fixture</html>')
    monkeypatch.setenv('NAIYUE_WEB_TOKEN', 'exit-test-access')
    monkeypatch.setenv('NAIYUE_WEB_PORT', '0')
    monkeypatch.setenv('NAIYUE_WEB_HOST', '127.0.0.1')
    monkeypatch.setattr(entry, 'create_web_app', lambda _: None)
    def stopped(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(entry.uvicorn.Server, 'run', stopped)
    assert entry.main(['--config', 'unused', '--frontend-dist', str(tmp_path)]) == 130
    assert '收到退出信号' in capsys.readouterr().out
