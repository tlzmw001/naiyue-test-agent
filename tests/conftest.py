"""Keep automatic tool-setting discovery independent of developer configuration."""

import pytest


@pytest.fixture(autouse=True)
def isolated_tool_settings(monkeypatch, tmp_path, request):
    from naiyue_test_agent.tools import settings
    monkeypatch.setattr(settings, 'get_agent_dir', lambda: tmp_path / 'agent-settings')

    entry_modules = {
        'test_application_cli', 'test_application_cli_read_tools', 'test_application_edit_cli',
        'test_application_write_cli', 'test_bash_output', 'test_cli_async_stop',
        'test_cli_event_integration', 'test_host_bash', 'test_persistent_cli',
        'test_session_system_prompt', 'test_tool_settings_entries', 'test_web_startup',
    }
    if request.module.__name__.split('.')[-1] in entry_modules:
        from pathlib import Path
        # Real cwd is now a workspace; entry tests must never initialize the developer's project.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
