"""Windows identity comparisons use pure paths; no fake Windows filesystem claims."""

from runtime_context_helpers import RunIdentity, ToolAuthorization, typed_context
from pathlib import PureWindowsPath
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException

from naiyue_test_agent.tools import paths, permissions, write
from naiyue_test_agent.tools.permission_rules import PermissionRule, PermissionSettings, match_rules


ENV = {'kind': 'host', 'platform': 'windows'}


def subject(path, *, root='C:/work', tool='read'):
    return dict(tool_name=tool, operation='read', workspace=root, requested=path, resolved=path,
                args={}, execution_environment=ENV)


@pytest.mark.parametrize('pattern,target', [
    ('C:/work/private.txt', r'\\?\C:\work\private.txt'),
    (r'\\?\C:\work\private.txt', 'c:/WORK/private.TXT'),
    ('${workspace}/private.txt', r'\\?\C:\work\private.txt'),
    (r'\\host\share\docs\**', r'\\?\UNC\HOST\SHARE\docs\a.txt'),
    (r'\\?\UNC\host\share\docs\**', r'\\HOST\share\docs\a.txt'),
    ('C:/work/**/a?.txt', r'\\?\C:\work\sub\a1.txt'),
])
@pytest.mark.parametrize('deny', [False, True])
def test_ordinary_extended_and_unc_rules_are_equivalent(pattern, target, deny):
    rule = PermissionRule(tool='read', path=pattern)
    assert match_rules([rule], subject(target), deny=deny) is rule


@pytest.mark.parametrize('target', [r'\\?\C:\work2\file', r'\\?\D:\work\file'])
def test_component_and_drive_boundaries_remain_distinct(target):
    rule = PermissionRule(tool='read', path='C:/work/**')
    assert match_rules([rule], subject(target), deny=True) is None


def test_windows_ordinary_and_extended_targets_have_one_comparison_value():
    assert paths.permission_path(r'\\?\C:\work\file', windows=True) == paths.permission_path('c:/WORK/file', windows=True)
    assert paths.permission_path(r'\\?\UNC\host\share\file', windows=True) == paths.permission_path(r'\\HOST\share\file', windows=True)
    assert paths.permission_path('/A/File', windows=False) != paths.permission_path('/a/file', windows=False)


@pytest.mark.parametrize('target,decision', [
    (r'\\?\C:\work\file', 'allow'),
    (r'\\?\C:\work2\file', 'ask'),
    (r'\\?\C:\work\.hidden\file', 'ask'),
    (r'\\?\C:\work\private.key', 'ask'),
])
def test_default_strategy_uses_same_workspace_and_hidden_comparison(target, decision):
    assert permissions.FilePermission().default_decision(subject(target)) == decision


def test_known_skill_resource_uses_same_comparison():
    value = subject(r'\\?\C:\work\.agents\skills\skill\SKILL.md')
    value['resource_roots'] = ['C:/work/.agents/skills']
    assert permissions.FilePermission().default_decision(value) == 'allow'
    value['requested'] = value['resolved'] = r'\\?\C:\work\.agents\skills\skill\.private'
    assert permissions.FilePermission().default_decision(value) == 'ask'


def test_session_allow_is_exact_target_with_normalized_workspace():
    approved = subject('C:/work/file')
    later = subject(r'\\?\C:\work\file', root=r'\\?\C:\work')
    remembered = permissions.remembered_rule(approved)
    assert permissions.matches_session_allow([remembered], later)
    later['resolved'] = r'\\?\C:\work\different'
    assert not permissions.matches_session_allow([remembered], later)


@pytest.mark.parametrize('path', [
    r'\\.\PhysicalDrive0', r'\\?\GLOBALROOT\Device\HarddiskVolume1\file',
    r'\\?\Volume{1234}\file', r'C:\work\file:stream', r'C:\work\NUL.txt',
    r'C:\work\COM¹.txt', r'C:\work\tail.', 'C:\\work\\tail ',
    r'\\?\C:\work\..\file', r'\\?\C:\work\.\file', r'C:relative', r'\rooted',
])
def test_unsupported_windows_targets_are_not_normalized_to_another_file(path):
    with pytest.raises(ValueError):
        paths.permission_path(path, windows=True)


@pytest.mark.parametrize('path', [r'\\.\PhysicalDrive0', r'\\?\C:\work\file:stream', r'C:\work\NUL.txt', r'C:\work\tail.'])
def test_unsupported_windows_rules_fail_configuration(path):
    with pytest.raises(ValueError):
        PermissionRule(tool='read', path=path)


def test_namespace_prefix_is_syntax_but_wildcard_remains_a_pattern():
    rule = PermissionRule(tool='read', path=r'\\?\C:\work\**\a?.txt')
    assert match_rules([rule], subject('C:/work/sub/a1.txt'), deny=True) is rule
    with pytest.raises(ValueError):
        paths.permission_path(r'\\?\C:\work\a?.txt', windows=True)


@pytest.mark.parametrize('name,expected', [
    ('private.txt', (False, 'deny')), ('plain.txt', (True, None)),
    ('.hidden', (False, 'unapproved_private_or_external')),
])
def test_recursive_candidates_keep_deny_and_hidden_rules(monkeypatch, name, expected):
    parent = subject(r'\\?\C:\work', tool='grep')
    parent.update(operation='search', path_field='path')
    child = subject(r'\\?\C:\work' + '/' + name, tool='grep')
    monkeypatch.setattr(paths, 'prepare_file_subject', lambda *a, **kw: dict(child))
    monkeypatch.setattr(permissions, 'execution_environment', lambda: ENV)
    context = typed_context(identity=RunIdentity(session_id='S'), authorization=ToolAuthorization(permission_rules=PermissionSettings(deny=(PermissionRule(tool='grep', path='C:/work/private.txt'),)), session_allow=[], permit={'call_id': 'call', 'session_id': 'S', 'agent_path': 'root', 'subject': parent}))
    runtime = SimpleNamespace(context=context, tool_call_id='call')
    assert permissions.candidate_access(runtime, child['resolved'], operation='search') == expected


def test_same_windows_target_cannot_reserve_two_prefix_forms(monkeypatch):
    monkeypatch.setattr(write, 'permission_path', lambda p: paths.permission_path(p, windows=True), raising=False)
    with write._reserve_target(PureWindowsPath('C:/work/file')):
        with pytest.raises(ToolException, match='busy'):
            with write._reserve_target(PureWindowsPath(r'\\?\C:\work\file')):
                pytest.fail('the second prefix acquired another lock')


@pytest.mark.parametrize('path', [r'C:\work\NUL .txt', r'\\host\share:stream\file', '\\\\ho\x00st\\share\\file'])
def test_reserved_and_stream_syntax_cannot_hide_in_components(path):
    with pytest.raises(ValueError):
        paths.permission_path(path, windows=True)


def test_parent_components_do_not_turn_an_external_target_into_workspace_child():
    value = paths.permission_path(r'C:\work\..\outside\file', windows=True)
    assert not value.is_relative_to(paths.permission_path('C:/work', windows=True))


@pytest.mark.parametrize('path', [r'\\.\PhysicalDrive0', r'C:\work\file:stream', r'\\?\C:\work\..\file'])
def test_unsupported_native_windows_input_fails_before_target_io(tmp_path, monkeypatch, path):
    import os
    from pathlib import Path
    proxy = SimpleNamespace(**vars(os))
    proxy.name = 'nt'
    monkeypatch.setattr(paths, 'os', proxy)
    monkeypatch.setattr(Path, 'lstat', lambda *_a, **_kw: pytest.fail('invalid input reached filesystem probe'))
    with pytest.raises(ToolException):
        paths.prepare_file_subject(path, tmp_path, 'read')
