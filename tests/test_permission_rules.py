"""Strict permission configuration and component-based path rules."""

import pytest

from naiyue_test_agent.application import prepare_tool_registry
from naiyue_test_agent.tools import create_session_read_tool, create_session_bash_tool, ToolSettings
from naiyue_test_agent.tools.permission_rules import PermissionRule, match_rules, validate_permission_rules
from naiyue_test_agent.tools.settings import load_tool_settings


@pytest.mark.parametrize('pattern,path,matched', [
    ('/root/*', '/root/a', True), ('/root/*', '/root/sub/a', False),
    ('/root/**', '/root/sub/a', True), ('/root/**/a?.txt', '/root/a1.txt', True),
    ('/root/**', '/root-other/a', False), ('/root/**', '/root/.hidden/a', True),
])
def test_component_glob(pattern, path, matched):
    rule = PermissionRule(tool='read', path=pattern)
    subject = {'tool_name': 'read', 'workspace': '/root', 'requested': path, 'resolved': path, 'args': {}}
    assert bool(match_rules((rule,), subject, deny=False)) is matched


@pytest.mark.parametrize('rule', [
    {'tool': 'missing'}, {'tool': 'bash', 'path': '/root/**'},
    {'tool': 'read', 'args': {'runtime': 'bad'}}, {'tool': 'read', 'args': {'offset': True}},
    {'tool': 'read', 'path': 'relative/**'}, {'tool': 'read', 'path': '${unknown}/**'},
    {'tool': 'read', 'path': '/root/[a]'}, {'tool': 'read', 'path': '/root/**x'},
])
def test_invalid_rule_rejected_before_agent(rule):
    registry = prepare_tool_registry([create_session_read_tool(), create_session_bash_tool()])
    with pytest.raises(ValueError):
        settings = ToolSettings.model_validate({'permissions': {'allow': [rule]}})
        validate_permission_rules(settings.permissions, registry.select(registry.names()))


def test_allow_requires_real_target_but_deny_matches_either():
    subject = {'tool_name': 'read', 'workspace': '/root', 'requested': '/root/link', 'resolved': '/outside/key', 'args': {}}
    rules = (PermissionRule(tool='read', path='${workspace}/**'),)
    assert match_rules(rules, subject, deny=False) is None
    assert match_rules(rules, subject, deny=True) is rules[0]


def test_command_is_exact_not_prefix():
    rule = PermissionRule(tool='bash', args={'command': 'python -m pytest'})
    subject = {'tool_name': 'bash', 'args': {'command': 'python -m pytest; echo BAD'}}
    assert match_rules((rule,), subject, deny=False) is None


@pytest.mark.parametrize('content', ['invalid = [', '[permissions]\nallow=[{tool="read",args={limit=[]}}]', '[permissions]\nmode="Auto"'])
def test_permission_or_file_syntax_error_never_falls_back(tmp_path, content):
    path = tmp_path / 'settings.toml'
    path.write_text(content)
    with pytest.raises(ValueError):
        load_tool_settings(path)


def test_bad_tool_budget_preserves_valid_permissions(tmp_path):
    path = tmp_path / 'settings.toml'
    path.write_text('[permissions]\nmode="NoAsk"\n[tools.read]\nmax_limit=0\n')
    loaded = load_tool_settings(path)
    assert loaded.permissions.mode == 'NoAsk' and loaded.tools.read.max_limit == 2000
