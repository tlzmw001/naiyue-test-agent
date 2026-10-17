"""One built-in collection for CLI and Web startup."""

from .bash import create_session_bash_tool
from .powershell import create_session_powershell_tool
from .edit import create_session_edit_tool
from .find import create_session_find_tool
from .grep import create_session_grep_tool
from .ls import create_session_ls_tool
from .read import create_session_read_tool
from .registry import ToolSpec
from .settings import ToolSettings
from .write import create_session_write_tool


def create_builtin_tools(settings: ToolSettings) -> list[ToolSpec]:
    specs = [
        create_session_read_tool(settings=settings.tools.read, progress_settings=settings.progress),
        create_session_ls_tool(settings=settings.tools.ls, progress_settings=settings.progress),
        create_session_find_tool(settings=settings.tools.find, process_settings=settings.process, progress_settings=settings.progress),
        create_session_grep_tool(settings=settings.tools.grep, process_settings=settings.process, progress_settings=settings.progress),
        create_session_write_tool(settings=settings.tools.write, progress_settings=settings.progress),
        create_session_edit_tool(settings=settings.tools.edit, progress_settings=settings.progress),
        create_session_bash_tool(process_settings=settings.process,
            settings=settings.tools.bash, output_settings=settings.tool_output, progress_settings=settings.progress),
        create_session_powershell_tool(process_settings=settings.process,
            settings=settings.tools.powershell, output_settings=settings.tool_output, progress_settings=settings.progress),
    ]
    return specs
