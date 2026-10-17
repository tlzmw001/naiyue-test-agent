"""Cooperative safe-point checks; resource ownership remains with each tool."""

from langchain_core.tools import ToolException


def check_tool_stop(control, feedback: str) -> None:
    """Use the existing run signal, preserving the tool's prepared feedback."""
    if control is not None and control.requested:
        raise ToolException(feedback)
