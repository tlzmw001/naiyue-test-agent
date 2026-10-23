"""One built-in collection for CLI and Web startup."""

from .bash import create_session_bash_tool
from .powershell import create_session_powershell_tool
from .edit import create_session_edit_tool
from .find import create_session_find_tool
from .grep import create_session_grep_tool
from .ls import create_session_ls_tool
from .read import create_session_read_tool
from .registry import ToolSpec
from .settings import BuiltinToolSettings
from .subagent import create_subagent_tool, create_subagent_list_tool
from .write import create_session_write_tool
from .aitest_kit import create_aitest_kit_tool
from .web_search import create_web_search_tool
from .web_fetch import create_web_fetch_tool
from .background_tasks import create_background_task_tools
from .history import create_history_tool
from .memory import create_memory_tool


def create_builtin_tools(settings: BuiltinToolSettings, *, process_settings=None, shell_task_settings=None, progress_settings=None, network_settings=None, retry_settings=None) -> list[ToolSpec]:
    specs = [
        create_session_read_tool(settings=settings.read, progress_settings=progress_settings),
        create_session_ls_tool(settings=settings.ls, progress_settings=progress_settings),
        create_session_find_tool(settings=settings.find, process_settings=process_settings, progress_settings=progress_settings),
        create_session_grep_tool(settings=settings.grep, process_settings=process_settings, progress_settings=progress_settings),
        create_session_write_tool(settings=settings.write, progress_settings=progress_settings),
        create_session_edit_tool(settings=settings.edit, progress_settings=progress_settings),
        create_session_bash_tool(process_settings=process_settings,
            settings=settings.bash, shell_task_settings=shell_task_settings, progress_settings=progress_settings),
        create_session_powershell_tool(process_settings=process_settings,
            settings=settings.powershell, shell_task_settings=shell_task_settings, progress_settings=progress_settings),
        create_subagent_tool(),
        create_subagent_list_tool(),
        create_aitest_kit_tool(settings=settings.aitest_kit, process_settings=process_settings,
            progress_settings=progress_settings),
        create_web_search_tool(settings=settings.web_search, network_settings=network_settings, retry_settings=retry_settings,
            progress_settings=progress_settings),
        create_web_fetch_tool(settings=settings.web_fetch, network_settings=network_settings, retry_settings=retry_settings,
            progress_settings=progress_settings),
    ]
    return [*specs, *create_background_task_tools(), create_history_tool(settings=settings.history), create_memory_tool()]
