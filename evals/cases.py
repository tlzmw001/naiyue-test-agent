"""Dataset contract shared by execution and scoring."""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


from .collaboration import DelegationExpectation
from .multi_turn import SimulationSpec
from .trajectory import TrajectorySpec
from .trajectory_workspace import canonical_artifact


class ExpectedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    args: dict[str, JsonValue]


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call: ExpectedToolCall
    result: str = Field(min_length=1)
    status: Literal["success", "error"] = "success"


class FileGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(pattern=r"^[A-Za-z0-9_-]+[.][A-Za-z0-9]+$")
    initial: str
    expected: str
    observed_line: str | None = None


class Checks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no_tools: Literal[True] | None = None
    clarify_first: Literal[True] | None = None
    required_reads: tuple[str, ...] | None = None
    final_ids: tuple[str, ...] | None = None
    rubric: str | None = None
    subagent_routes: tuple[str, ...] | None = None
    tool_call: ExpectedToolCall | None = None
    plan_answer: str | None = None
    plan_outcome: Literal["completed", "blocked"] | None = None


class RoleDescription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-zA-Z0-9_.-]+$")
    description: str = Field(min_length=1)


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    mode: Literal["model", "session", "planning"]
    turns: tuple[str, ...] = Field(min_length=1)
    history: tuple[tuple[Literal["user", "assistant"], str], ...] = ()
    tools: tuple[str, ...] = ()
    roles: tuple[RoleDescription, ...] = ()
    fixture: Path | None = None
    simulation: SimulationSpec | None = None
    collaboration: tuple[DelegationExpectation, ...] | None = None
    trajectory: TrajectorySpec | None = None
    file_goal: FileGoal | None = None
    plan_steps: tuple[PlanStep, ...] = Field(default=(), max_length=6)
    attempts: Literal[1, 4] = 1
    checks: Checks


def load_cases(dataset: Path) -> list[EvalCase]:
    from .tool_specs import component_tools

    supported_tools = component_tools().keys()
    cases = []
    seen = set()
    base = dataset.resolve().parent
    for number, line in enumerate(dataset.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            case = EvalCase.model_validate(raw)
            if case.id in seen:
                raise ValueError(f"duplicate ID {case.id}")
            if not case.trajectory and not any(value is not None for value in case.checks.model_dump().values()):
                raise ValueError("missing checks")
            if case.mode == "model" and (len(case.turns) != 1 or case.fixture):
                raise ValueError("model mode requires one turn and no fixture")
            if case.checks.clarify_first and len(case.turns) < 2 and not case.simulation:
                raise ValueError("clarification requires multiple turns")
            if len(set(case.tools)) != len(case.tools) or set(case.tools) - supported_tools:
                raise ValueError("tools must be distinct supported tool names")
            if case.mode == "planning":
                if (len(case.turns) != 1 or case.fixture or bool(case.plan_steps) == bool(case.file_goal)
                        or case.checks.plan_outcome is None):
                    raise ValueError("planning requires one user request, either scripted steps or a file goal, and an outcome")
                if case.file_goal and (not case.tools or set(case.tools) - {"read", "edit", "write"}
                                       or case.checks.plan_outcome != "completed"):
                    raise ValueError("file goals require local file tools and a completed outcome")
                for step in case.plan_steps:
                    if step.call.name not in case.tools:
                        raise ValueError("each planning step requires an available tool")
                    component_tools()[step.call.name].validate_input(step.call.args)
            elif case.plan_steps or case.file_goal or case.checks.plan_outcome or case.checks.plan_answer:
                raise ValueError("plan_steps and plan_outcome require planning mode")
            if case.simulation:
                if (case.mode != "session" or case.attempts != 4 or len(case.turns) != 1
                        or case.collaboration is not None or case.trajectory or not case.fixture
                        or case.checks.final_ids is None or case.checks.rubric):
                    raise ValueError("simulation requires one opening, four session attempts and a code-graded outcome")
            if case.collaboration is not None:
                expected_roles = [item.role for item in case.collaboration]
                if (case.mode != "session" or case.trajectory or not case.fixture or len(case.turns) != 1
                        or not case.roles or set(case.tools) != {"subagent", "read"}
                        or (case.checks.final_ids is None) == (case.checks.rubric is None)
                        or any((item.report_lines is None) != (case.checks.rubric is not None)
                               for item in case.collaboration)
                        or len(set(expected_roles)) != len(expected_roles)
                        or set(expected_roles) - {role.name for role in case.roles}
                        or any(item.read.name != "read" for item in case.collaboration)):
                    raise ValueError("collaboration requires a session fixture, role expectations and a checkable answer")
            elif case.trajectory:
                trace = case.trajectory
                if case.mode != "session" or not case.fixture or case.checks.rubric:
                    raise ValueError("trajectory requires an isolated session fixture and code grading")
                if set(case.tools) - {"read", "ls", "grep", "find", "edit", "write"}:
                    raise ValueError("unsupported trajectory tool")
                if not trace.artifact and case.checks.final_ids is None:
                    raise ValueError("trajectory needs an independently checkable outcome")
                if trace.artifact:
                    canonical_artifact(trace.artifact.format, trace.artifact.content)
                for reference in trace.references:
                    for stage in reference:
                        if any(s.name not in case.tools for s in stage):
                            raise ValueError("reference uses an unavailable tool")
            elif case.mode == "session" and set(case.tools) - {"read"}:
                raise ValueError("session mode currently supports only read")
            call = case.checks.tool_call
            if call is not None and (case.mode != "model" or call.name not in case.tools
                                     or case.checks.no_tools or case.checks.subagent_routes is not None):
                raise ValueError("tool_call requires a visible tool in model mode and no conflicting checks")
            role_names = {role.name for role in case.roles}
            if len(role_names) != len(case.roles) or any(not role.description.strip() for role in case.roles):
                raise ValueError("roles must have distinct names and nonempty descriptions")
            if case.roles or "subagent" in case.tools or case.checks.subagent_routes is not None:
                if (case.mode != "model" and case.collaboration is None) or not role_names or "subagent" not in case.tools:
                    raise ValueError("subagent routing requires model mode, roles and the subagent tool")
                routes = case.checks.subagent_routes
                if routes is not None and (case.collaboration is not None or not routes or set(routes) - role_names or case.checks.no_tools):
                    raise ValueError("expected delegation must reference visible roles and allow calls")
            if case.fixture:
                fixture = (base / case.fixture).resolve()
                if fixture == base or not fixture.is_relative_to(base) or not fixture.is_dir():
                    raise ValueError("fixture must be a directory inside the dataset directory")
                if any(path.is_symlink() for path in fixture.rglob("*")):
                    raise ValueError("fixture must not contain symlinks")
                case = case.model_copy(update={"fixture": fixture})
        except (ValueError, TypeError) as error:
            raise ValueError(f"{dataset}:{number}: {error}") from error
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise ValueError("dataset contains no cases")
    return cases
