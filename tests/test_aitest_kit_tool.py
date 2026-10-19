"""AITest is an argv wrapper, with existing approval and output contracts."""

import asyncio
import sys

from tool_feedback_helpers import invoke_tool_feedback, EXPECTED_NOASK


def test_aitest_args_are_literal_and_doctor_report_default_allow(tmp_path):
    from naiyue_test_agent.tools.aitest_kit import create_aitest_kit_tool
    from naiyue_test_agent.tools.settings import AITestSettings
    settings = AITestSettings(executable=sys.executable,
        prefix_args=['-c', 'import sys; print(repr(sys.argv[1:]))'])
    spec = create_aitest_kit_tool(settings=settings)
    for command in ('doctor', 'report'):
        result = asyncio.run(invoke_tool_feedback(spec, {'args': [command, 'literal; $(no-shell)']},
            call_id='aitest', context={'workspace_root': tmp_path}))
        assert result.status == 'success' and 'literal; $(no-shell)' in result.content
    for args in (['--help'], ['run', '--help']):
        result = asyncio.run(invoke_tool_feedback(spec, {'args': args}, call_id='aitest',
            context={'workspace_root': tmp_path}))
        assert result.status == 'success'
    blocked = asyncio.run(invoke_tool_feedback(spec, {'args': ['run']},
        call_id='aitest', context={'workspace_root': tmp_path}))
    assert blocked.status == 'error' and blocked.content == EXPECTED_NOASK
    assert spec.execution_mode == 'sequential' and spec.side_effects == 'may_mutate'
    assert '.claude/skills' in ' '.join(spec.prompt_guidelines)


def test_aitest_nonzero_output_is_tool_feedback(tmp_path):
    from naiyue_test_agent.tools.aitest_kit import create_aitest_kit_tool
    from naiyue_test_agent.tools.settings import AITestSettings
    spec = create_aitest_kit_tool(settings=AITestSettings(executable=sys.executable,
        prefix_args=['-c', 'import sys; print("actual output"); sys.exit(7)']))
    result = asyncio.run(invoke_tool_feedback(spec, {'args': ['doctor']}, call_id='aitest',
        context={'workspace_root': tmp_path}))
    assert result.status == 'error' and 'exit_code=7' in result.content and 'actual output' in result.content


def test_help_as_an_option_value_or_passthrough_does_not_grant_permission(tmp_path):
    from naiyue_test_agent.tools.aitest_kit import create_aitest_kit_tool
    from naiyue_test_agent.tools.settings import AITestSettings
    spec = create_aitest_kit_tool(settings=AITestSettings(executable=sys.executable,
        prefix_args=['-c', 'raise AssertionError("must not execute")']))
    for args in (['init', '--target', '--help', '--force'], ['run', '--', '--help']):
        result = asyncio.run(invoke_tool_feedback(spec, {'args': args}, call_id='aitest',
            context={'workspace_root': tmp_path}))
        assert result.content == EXPECTED_NOASK


def test_effective_path_lookup_preserves_windows_key_and_removed_path(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import aitest_kit as module
    for environment, expected in (({'Path': '/selected/tools'}, '/selected/tools'), ({}, '')):
        monkeypatch.setattr(module, 'build_environment', lambda *a, **k: environment)
        seen = []
        def which(executable, *, path):
            seen.append(path)
            return None
        monkeypatch.setattr(module.shutil, 'which', which)
        result = asyncio.run(invoke_tool_feedback(module.create_aitest_kit_tool(), {'args': ['doctor']},
            call_id='aitest', context={'workspace_root': tmp_path}))
        assert result.status == 'error' and seen == [expected]
