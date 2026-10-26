"""Storage can load alone; SQL and application policy have distinct owners."""

import ast
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'naiyue_test_agent'
SQL = re.compile(r'\b(?:SELECT\s+.+?\s+FROM|INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+(?:VIRTUAL\s+)?(?:TABLE|INDEX|TRIGGER)|ALTER\s+TABLE|PRAGMA\s+)\b', re.I | re.S)


def imports(source, package):
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            if node.level:
                module = importlib.util.resolve_name('.' * node.level + module, package)
            found.append(module)
            found.extend(module + '.' + item.name for item in node.names)
        elif isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            if name in ('__import__', 'import_module') and node.args and isinstance(node.args[0], ast.Constant):
                target = node.args[0].value
                if isinstance(target, str):
                    if target.startswith('.'):
                        target = importlib.util.resolve_name(target, package)
                    found.append(target)
    return found


def test_storage_has_no_application_imports_including_local_or_dynamic_imports():
    violations = []
    for path in (PACKAGE / 'storage').rglob('*.py'):
        package = '.'.join(path.relative_to(ROOT).with_suffix('').parts[:-1])
        for name in imports(path.read_text(), package):
            if name == 'naiyue_test_agent.application' or name.startswith('naiyue_test_agent.application.'):
                violations.append(f'{path.relative_to(ROOT)}: {name}')
    assert not violations, '\n'.join(violations)


def test_all_storage_modules_import_in_fresh_process_without_loading_application():
    program = '''
import importlib, pathlib, sys
root = pathlib.Path('naiyue_test_agent/storage')
for path in sorted(root.rglob('*.py')):
    parts = path.with_suffix('').parts
    if parts[-1] == '__init__': parts = parts[:-1]
    importlib.import_module('.'.join(parts))
loaded = [name for name in sys.modules if name == 'naiyue_test_agent.application' or name.startswith('naiyue_test_agent.application.')]
assert not loaded, loaded
'''
    result = subprocess.run([sys.executable, '-c', program], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def sql_nodes(source):
    return [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant)
            and isinstance(node.value, str) and SQL.search(node.value)]


def test_application_does_not_construct_database_sql():
    violations = []
    for path in (PACKAGE / 'application').rglob('*.py'):
        violations.extend(f'{path.relative_to(ROOT)}:{node.lineno}' for node in sql_nodes(path.read_text()))
    assert not violations, '\n'.join(violations)


@pytest.mark.parametrize('source', [
    'from ..application.errors import SessionPersistenceError',
    'def local():\n from naiyue_test_agent.application import errors',
    "import importlib\nimportlib.import_module('naiyue_test_agent.application.errors')",
    "__import__('naiyue_test_agent.application.errors')",
])
def test_import_guard_detects_supported_escape_shapes(source):
    assert any(name.startswith('naiyue_test_agent.application')
        for name in imports(source, 'naiyue_test_agent.storage'))


def test_sql_guard_distinguishes_execution_methods_from_queries():
    assert not sql_nodes('async def _execute():\n return await runs.execute()')
    assert sql_nodes('sql = "SELECT value FROM saved WHERE id=?"')
    assert sql_nodes('sql = f"INSERT INTO {table}(value) VALUES(?)"')
