"""The controller's public tools remain available without eager execution imports."""
import importlib
from pathlib import Path
import subprocess
import sys

import pytest


def test_execution_import_does_not_load_unrelated_tool_factories():
    program = '''
import sys
import naiyue_test_agent.envd.operations
for name in ('builtin', 'subagent', 'history', 'memory', 'web_search', 'web_fetch'):
    assert 'naiyue_test_agent.tools.' + name not in sys.modules, name
'''
    result = subprocess.run([sys.executable, '-c', program],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('name,module', [
    ('ReadSettings', 'settings'), ('ReadImageSettings', 'settings'),
    ('ToolRegistry', 'registry'), ('SubAgentSpec', 'subagent'),
    ('create_builtin_tools', 'builtin'), ('create_session_read_tool', 'read'),
    ('create_web_fetch_tool', 'web_fetch'), ('WebFetchSettings', 'web_settings'),
])
def test_existing_tool_export_keeps_original_object(name, module):
    import naiyue_test_agent.tools as tools
    original = getattr(importlib.import_module('naiyue_test_agent.tools.' + module), name)
    assert getattr(tools, name) is original
    assert getattr(tools, name) is original


def test_star_import_and_unknown_tool_export_keep_existing_contract():
    import naiyue_test_agent.tools as tools
    namespace = {}
    exec('from naiyue_test_agent.tools import *', namespace)
    assert set(namespace) - {'__builtins__'} == set(tools.__all__)
    assert namespace['create_builtin_tools'] is tools.create_builtin_tools
    with pytest.raises(AttributeError):
        getattr(tools, 'not_a_tool_export')
