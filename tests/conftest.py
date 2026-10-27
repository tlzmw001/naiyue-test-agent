"""Keep automatic application-setting discovery independent of developer configuration."""

import pytest


@pytest.fixture(autouse=True)
def isolated_tool_settings(monkeypatch, tmp_path, request):
    from pathlib import Path
    from naiyue_test_agent import settings
    monkeypatch.setattr(settings, 'get_agent_dir', lambda: tmp_path / 'agent-settings')
    # Application archives now use configured data storage rather than command cwd.
    isolated_home = tmp_path.parent / (tmp_path.name + '-home')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: isolated_home))

    entry_modules = {
        'test_application_cli', 'test_application_cli_read_tools', 'test_application_edit_cli',
        'test_application_write_cli', 'test_bash_output', 'test_cli_async_stop',
        'test_cli_event_integration', 'test_host_bash', 'test_persistent_cli',
        'test_session_system_prompt', 'test_tool_settings_entries', 'test_web_startup',
    }
    module = request.module.__name__.split('.')[-1]
    if module in entry_modules:
        # Real cwd is now a workspace; entry tests must never initialize the developer's project.
        monkeypatch.chdir(tmp_path)
    if module in {'test_persistent_cli', 'test_tool_settings_entries'}:
        # These suites explicitly assert the default HOME-based paths.
        monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))


@pytest.fixture(autouse=True)
def terminal_entry_service(monkeypatch, request):
    modules = {'test_application_cli', 'test_application_cli_read_tools', 'test_application_edit_cli',
        'test_application_write_cli', 'test_bash_output', 'test_cli_async_stop',
        'test_cli_event_integration', 'test_host_bash', 'test_persistent_cli',
        'test_session_system_prompt', 'test_tool_settings_entries', 'test_cli_subagents',
        'test_cli_subagent_stop', 'test_temporary_application_storage', 'test_storage_startup_entries',
        'test_workspace_storage_entries', 'test_history_recovery_boundaries', 'test_public_history_recovery',
        'test_subagent_settings', 'test_shell_task_settings', 'test_cli_background_tasks',
        'test_cli_observation', 'test_distribution_runtime'}
    if request.module.__name__.split('.')[-1] not in modules:
        return
    from naiyue_test_agent.cli import chat
    from terminal_service_helpers import isolated_terminal_launch
    original = chat.main
    def run(argv=None):
        # Existing entry cases explicitly authorize their temporary fixture root.
        # Trust prompt behavior is checked separately through the public flags.
        with isolated_terminal_launch(monkeypatch):
            return original([*(argv or ()), '--trust-project'])
    monkeypatch.setattr(chat, 'main', run)
