"""Register the environment-bound bash tool."""
from .settings import BashSettings
from .shell import build_shell_tool


def create_session_bash_tool(*, process_settings=None, settings=None, shell_task_settings=None, progress_settings=None):
    return build_shell_tool('bash', settings or BashSettings(), process_settings, shell_task_settings, progress_settings)
