"""Domain ownership and real command entry points after the package migration."""

import ast
import importlib
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('module,symbol', [
    ('application.sessions.agent_session', 'AgentSession'),
    ('application.conversation.history_queries', 'query'),
    ('application.subagents.subagent_records', 'role_fingerprint'),
    ('application.observability.run_events', 'RunObservation'),
    ('storage.memory', 'MemoryRepo'),
    ('storage.project_store', 'ProjectStore'),
    ('cli.cli_async', 'AsyncCliApplication'),
])
def test_domain_implementation_has_one_current_import_path(module, symbol):
    loaded = importlib.import_module('naiyue_test_agent.' + module)
    assert callable(getattr(loaded, symbol))


def test_storage_first_import_keeps_application_exports_and_identity():
    code = '''from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.application import SessionService, SessionBusyError
from naiyue_test_agent.application.sessions.session_service import SessionService as Actual
from naiyue_test_agent.application.errors import SessionBusyError as ActualError
assert SessionService is Actual and SessionBusyError is ActualError
assert ProjectStore.__module__ == 'naiyue_test_agent.storage.project_store'
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_command_dispatch_import_does_not_assemble_an_application():
    code = '''import sys
import naiyue_test_agent.cli
import naiyue_test_agent.cli.storage
assert not any(name.startswith(('naiyue_test_agent.application', 'naiyue_test_agent.agent_runtime'))
               for name in sys.modules)
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('args,label', [
    (['naiyue_test_agent.cli', '--help'], 'nyt'),
    (['naiyue_test_agent.cli', 'chat', '--help'], '--no-session'),
    (['naiyue_test_agent.cli', 'storage', '--help'], 'restore'),
    (['naiyue_test_agent.cli', 'mcp', '--help'], 'list'),
    (['naiyue_test_agent.application.cli', '--help'], '--no-session'),
])
def test_documented_commands_keep_their_real_entry_points(args, label):
    result = subprocess.run([sys.executable, '-m', *args], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert label in result.stdout


def test_application_domains_do_not_depend_on_cli_or_web_adapters():
    root = ROOT / 'naiyue_test_agent' / 'application'
    expected = {'sessions', 'conversation', 'subagents', 'observability', 'memory'}
    assert expected.issubset({path.name for path in root.iterdir() if path.is_dir()})
    assert {path.name for path in root.glob('*.py')} == {
        '__init__.py', 'async_application.py', 'async_persistent.py', 'assembly.py', 'core.py', 'lifetime.py', 'catalogs.py', 'errors.py', 'cli.py'}
    for folder in expected:
        for path in (root / folder).glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.module or '').startswith(('naiyue_test_agent.cli', 'naiyue_test_agent.web')), path
                elif isinstance(node, ast.Import):
                    assert not any(item.name.startswith(('naiyue_test_agent.cli', 'naiyue_test_agent.web'))
                                   for item in node.names), path


def test_only_the_documented_old_chat_launcher_remains():
    package = ROOT / 'naiyue_test_agent'
    assert not list(package.glob('storage_*.py'))
    assert not (package / 'cli.py').exists()
    assert not (package / 'mcp_client' / 'cli.py').exists()
    launcher = package / 'application' / 'cli.py'
    tree = ast.parse(launcher.read_text())
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                   for node in ast.walk(tree)), 'The public old command must not retain a second implementation'
