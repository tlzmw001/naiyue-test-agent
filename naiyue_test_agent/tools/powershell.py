"""Register the environment-bound powershell tool."""
from .settings import PowerShellSettings
from .shell import build_shell_tool


def create_session_powershell_tool(*, process_settings=None, settings=None, shell_task_settings=None, progress_settings=None):
    return build_shell_tool('powershell', settings or PowerShellSettings(), process_settings, shell_task_settings, progress_settings)
