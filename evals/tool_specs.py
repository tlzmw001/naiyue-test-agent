"""Product tool definitions for component evaluation without execution."""
from functools import cache

from naiyue_test_agent.cloud_runner.result_tool import create_result_directory_tool
from naiyue_test_agent.contracts.cloud_results import ResultLimits
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import create_builtin_tools


@cache
def component_tools():
    specs = [*create_builtin_tools(AppSettings().tools),
             create_result_directory_tool(ResultLimits())]
    return {spec.tool.name: spec for spec in specs}
