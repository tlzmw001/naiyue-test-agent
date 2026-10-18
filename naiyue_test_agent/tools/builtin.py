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
from .subagent import create_subagent_tool
from .write import create_session_write_tool


def create_builtin_tools(settings: BuiltinToolSettings, *, process_settings=None, output_settings=None, progress_settings=None) -> list[ToolSpec]:
    specs = [
        create_session_read_tool(settings=settings.read, progress_settings=progress_settings),
        create_session_ls_tool(settings=settings.ls, progress_settings=progress_settings),
        create_session_find_tool(settings=settings.find, process_settings=process_settings, progress_settings=progress_settings),
        create_session_grep_tool(settings=settings.grep, process_settings=process_settings, progress_settings=progress_settings),
        create_session_write_tool(settings=settings.write, progress_settings=progress_settings),
        create_session_edit_tool(settings=settings.edit, progress_settings=progress_settings),
        create_session_bash_tool(process_settings=process_settings,
            settings=settings.bash, output_settings=output_settings, progress_settings=progress_settings),
        create_session_powershell_tool(process_settings=process_settings,
            settings=settings.powershell, output_settings=output_settings, progress_settings=progress_settings),
        create_subagent_tool(),
    ]
    return specs
