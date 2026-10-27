"""Public controller tool exports; execution imports do not load the catalog."""
from importlib import import_module as _import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .settings import ReadSettings, ReadImageSettings, LsSettings, WriteSettings, EditSettings, GrepSettings, FindSettings, ProcessSettings
    from .registry import ToolSpec, ToolRegistry
    from .subagent import SubAgentSpec, create_subagent_tool, create_subagent_list_tool
    from .settings import EnvironmentSettings, ShellSettings, BashSettings, PowerShellSettings, ToolProgressSettings
    from .builtin import create_builtin_tools
    from .read import create_session_read_tool
    from .ls import create_session_ls_tool
    from .find import create_session_find_tool
    from .grep import create_session_grep_tool
    from .write import create_session_write_tool
    from .edit import create_session_edit_tool
    from .bash import create_session_bash_tool
    from .powershell import create_session_powershell_tool
    from .aitest_kit import create_aitest_kit_tool
    from .web_search import create_web_search_tool
    from .web_fetch import create_web_fetch_tool
    from .settings import AITestSettings
    from .web_settings import NetworkSettings, WebSearchSettings, WebFetchSettings

_EXPORTS = {'ReadSettings': ('settings', 'ReadSettings'),
 'ReadImageSettings': ('settings', 'ReadImageSettings'),
 'LsSettings': ('settings', 'LsSettings'),
 'WriteSettings': ('settings', 'WriteSettings'),
 'EditSettings': ('settings', 'EditSettings'),
 'GrepSettings': ('settings', 'GrepSettings'),
 'FindSettings': ('settings', 'FindSettings'),
 'ProcessSettings': ('settings', 'ProcessSettings'),
 'ToolSpec': ('registry', 'ToolSpec'),
 'ToolRegistry': ('registry', 'ToolRegistry'),
 'SubAgentSpec': ('subagent', 'SubAgentSpec'),
 'create_subagent_tool': ('subagent', 'create_subagent_tool'),
 'create_subagent_list_tool': ('subagent', 'create_subagent_list_tool'),
 'EnvironmentSettings': ('settings', 'EnvironmentSettings'),
 'ShellSettings': ('settings', 'ShellSettings'),
 'BashSettings': ('settings', 'BashSettings'),
 'PowerShellSettings': ('settings', 'PowerShellSettings'),
 'ToolProgressSettings': ('settings', 'ToolProgressSettings'),
 'create_builtin_tools': ('builtin', 'create_builtin_tools'),
 'create_session_read_tool': ('read', 'create_session_read_tool'),
 'create_session_ls_tool': ('ls', 'create_session_ls_tool'),
 'create_session_find_tool': ('find', 'create_session_find_tool'),
 'create_session_grep_tool': ('grep', 'create_session_grep_tool'),
 'create_session_write_tool': ('write', 'create_session_write_tool'),
 'create_session_edit_tool': ('edit', 'create_session_edit_tool'),
 'create_session_bash_tool': ('bash', 'create_session_bash_tool'),
 'create_session_powershell_tool': ('powershell', 'create_session_powershell_tool'),
 'create_aitest_kit_tool': ('aitest_kit', 'create_aitest_kit_tool'),
 'create_web_search_tool': ('web_search', 'create_web_search_tool'),
 'create_web_fetch_tool': ('web_fetch', 'create_web_fetch_tool'),
 'AITestSettings': ('settings', 'AITestSettings'),
 'NetworkSettings': ('web_settings', 'NetworkSettings'),
 'WebSearchSettings': ('web_settings', 'WebSearchSettings'),
 'WebFetchSettings': ('web_settings', 'WebFetchSettings')}

__all__ = ['ReadSettings', 'ReadImageSettings', 'LsSettings', 'WriteSettings', 'EditSettings', 'GrepSettings', 'FindSettings', 'ProcessSettings',
           'ToolSpec', 'ToolRegistry', 'SubAgentSpec', 'create_subagent_tool', 'create_subagent_list_tool', 'create_builtin_tools',
           'create_session_read_tool', 'create_session_ls_tool',
           'create_session_find_tool', 'create_session_grep_tool',
           'create_session_write_tool', 'create_session_edit_tool',
           'create_session_bash_tool', 'create_session_powershell_tool',
           'EnvironmentSettings', 'ShellSettings', 'BashSettings', 'PowerShellSettings', 'ToolProgressSettings',
           'AITestSettings', 'NetworkSettings', 'WebSearchSettings', 'WebFetchSettings',
           'create_aitest_kit_tool', 'create_web_search_tool', 'create_web_fetch_tool']


def __getattr__(name):
    source = _EXPORTS.get(name)
    if source is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    module, attribute = source
    value = getattr(_import_module('.' + module, __name__), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))
