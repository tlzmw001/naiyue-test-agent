"""CLI choices fetch the service fingerprint and cannot alter restored bindings."""
from argparse import Namespace

import pytest

from naiyue_test_agent.cli.chat import main
from naiyue_test_agent.cli import chat_startup
from naiyue_test_agent.service.settings import ServiceError
from naiyue_test_agent.settings import AppSettings


@pytest.mark.parametrize('args', [[], ['--execution', 'host'], ['--resume', 'S', '--execution', 'sandbox'],
    ['--no-session', '--execution', 'sandbox']])
def test_environment_requires_new_persistent_sandbox_before_connect(args):
    with pytest.raises(SystemExit) as caught: main([*args, '--environment', 'python-tests'])
    assert caught.value.code == 2


def test_cli_accepts_environment_for_new_sandbox(monkeypatch):
    from naiyue_test_agent.cli import chat
    seen = []
    def prepare(args, settings):
        seen.append((args.execution, args.environment)); return None, None, None
    monkeypatch.setattr(chat, 'prepare_terminal', prepare)
    monkeypatch.setattr(chat, 'load_settings', lambda: None)
    assert main(['--execution', 'sandbox', '--environment', 'frontend-tests']) == 0
    assert seen == [('sandbox', 'frontend-tests')]


@pytest.mark.parametrize('profile,code', [('frontend-tests', None), ('missing', 'env_profile_unavailable')])
def test_terminal_forwards_selected_profile_and_server_revision(tmp_path, monkeypatch, profile, code):
    calls = []; closed = []
    class Client:
        def __init__(self, options): pass
        def bind(self, scope): assert scope == 'P'
        def close(self): closed.append(True)
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == '/api/v1/terminal/prepare':
                return dict(status='ready', scope_id='P', root=str(tmp_path), source_kind='local')
            if path == '/models': return dict(default_model_id='fake')
            if path.endswith('/execution-options'):
                return dict(profiles=[dict(profile_id='frontend-tests', name='Node tests', revision='a'*64)])
            if path == '/sessions': return dict(session_id='S')
            if path == '/sessions/S': return dict(execution=dict(kind='sandbox', state='ready'))
            pytest.fail('unexpected request '+path)
    monkeypatch.setattr(chat_startup, 'TerminalClient', Client)
    args = Namespace(config=tmp_path/'models.json', settings=tmp_path/'settings.toml', no_session=False,
        workspace=tmp_path, project=None, session_db=None, resume=None, new_workspace=False,
        trust_project=True, execution='sandbox', include_untracked=['notes.txt'], environment=profile)
    if code:
        with pytest.raises(ServiceError, match=code): chat_startup.prepare_terminal(args, AppSettings())
        assert closed == [True] and not any(path == '/sessions' for _, path, _ in calls)
    else:
        client, sid, restored = chat_startup.prepare_terminal(args, AppSettings())
        assert sid == 'S' and restored is None
        sent = next(kwargs['json'] for _, path, kwargs in calls if path == '/sessions')
        assert sent == dict(model_id='fake', execution='sandbox', include_untracked=['notes.txt'],
            profile_id=profile, expected_profile_revision='a'*64)
        client.close()
