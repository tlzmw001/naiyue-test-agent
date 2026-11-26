"""Script tool responses or exercise file operations on a single temporary file."""
from pathlib import Path

from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException
from pydantic import ValidationError
from naiyue_test_agent.tools.edit import _edit
from naiyue_test_agent.tools.paths import prepare_file_subject
from naiyue_test_agent.tools.read import _read
from naiyue_test_agent.tools.settings import EditSettings, ReadSettings, WriteSettings
from naiyue_test_agent.tools.write import _write

from .scoring import check_tool_call
from .tool_specs import component_tools


def simulate_plan_batch(case, reply, completed):
    expected = case.plan_steps[completed] if completed < len(case.plan_steps) else None
    results = []
    for call in reply.tool_calls:
        if expected is not None and all(check_tool_call(expected.call, [call]).values()):
            results.append(ToolMessage(content=expected.result, status=expected.status,
                                       name=call["name"], tool_call_id=call["id"]))
            completed += 1
            expected = None
        else:
            results.append(ToolMessage(content="该工具请求与当前任务状态或约束不符，未执行。",
                                       status="error", name=call["name"], tool_call_id=call["id"],
                                       additional_kwargs={"plan_unexpected": True}))
    return completed, results


def run_file_batch(case, reply, root):
    goal = case.file_goal
    results = []
    for call in reply.tool_calls:
        name = call["name"]
        forbidden = False
        try:
            if name not in case.tools:
                raise ValueError("Tool unavailable")
            args = component_tools()[name].validate_input(call["args"])
            if Path(args.path) != Path(goal.path):
                raise ValueError("Only the specified workspace file is available")
            forbidden = name != "read" and goal.initial == goal.expected
            if name == "read":
                content = _read(root / goal.path, args.offset, args.limit, settings=ReadSettings())
            else:
                subject = prepare_file_subject(goal.path, root, "write")
                if name == "edit":
                    content = _edit(root, goal.path, args.edits, EditSettings(), subject=subject)
                else:
                    content = _write(root, goal.path, args.content, WriteSettings().max_input_bytes,
                                     subject=subject)
            status = "success"
        except (ValidationError, ValueError) as error:
            content, status, forbidden = str(error), "error", True
        except ToolException as error:
            content, status = str(error), "error"
        results.append(ToolMessage(content=content, status=status, name=name, tool_call_id=call["id"],
                                   additional_kwargs={"plan_unexpected": forbidden}))
    return results
