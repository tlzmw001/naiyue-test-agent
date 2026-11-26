"""Deterministic matching of observed tool calls to alternative staged references."""
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .scoring import arguments_match


class TraceStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    args: dict[str, JsonValue]
    status: Literal["success", "error"] = "success"
    result_contains: str | None = Field(default=None, min_length=1)


Stage = Annotated[tuple[TraceStep, ...], Field(min_length=1)]


class ArtifactExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(pattern=r"^[a-zA-Z0-9_-]+[.][a-zA-Z0-9]+$")
    format: Literal["python_ast", "csv_rows", "json", "boundary_asserts"]
    content: str


class TrajectorySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equivalence: str = Field(min_length=1)
    boundary: str = Field(min_length=1)
    references: tuple[tuple[Stage, ...], ...] = Field(min_length=1)
    artifact: ArtifactExpectation | None = None
    forbidden_tools: tuple[str, ...] = ()


def matches(step, call):
    if call.get("not_executed") or call["name"] != step.name or call.get("status") != step.status:
        return False
    args = call["args"]
    if "path" in step.args:
        path = call.get("workspace_path", args.get("path", "."))
        args = {**args, "path": str(PurePosixPath(path)) if isinstance(path, str) else None}
    if step.result_contains is not None:
        content = call.get("result")
        if not isinstance(content, str) or step.result_contains not in content:
            return False
    return arguments_match(args, step.args)


def assign(steps, calls, indices):
    """A broad step may yield its first match to a more specific step."""
    owners = {}

    def place(index, visited):
        for pos in indices:
            if pos in visited or not matches(steps[index], calls[pos]):
                continue
            visited.add(pos)
            if pos not in owners or place(owners[pos], visited):
                owners[pos] = index
                return True
        return False

    for index in range(len(steps)):
        place(index, set())
    return owners


def ordered_alignment(stages, calls):
    # Finish each independent stage at its earliest possible batch boundary.
    assigned = {}
    start = 0
    for stage in stages:
        for end in range(start + 1, len(calls) + 1):
            if end < len(calls) and calls[end]["batch"] == calls[end - 1]["batch"]:
                continue
            matched = assign(stage, calls, range(start, end))
            if len(matched) == len(stage):
                offset = len(assigned)
                assigned.update({pos: offset + i for pos, i in matched.items()})
                start = end
                break
        else:
            return None
    return assigned


def score_trajectory(spec, calls, done):
    best, best_key = None, None
    for index, stages in enumerate(spec.references):
        steps = [step for stage in stages for step in stage]
        owners = assign(steps, calls, range(len(calls)))
        count = len(owners)
        precision = count / len(calls) if calls else 1.0
        recall = count / len(steps) if steps else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
        if not done:
            grade = "partial" if count else "divergent"
        elif count != len(steps):
            grade = "review"
        else:
            alignment = ordered_alignment(stages, calls)
            if alignment is None:
                grade = "unordered"
            else:
                owners = alignment
                grade = "exact" if count == len(calls) else "ordered"
        ranks = {"review": 0, "divergent": 1, "partial": 2, "unordered": 3, "ordered": 4, "exact": 5}
        key = ranks[grade], f1, count
        if best_key is None or key > best_key:
            score = {"review": None, "divergent": 0, "partial": 20+20*f1, "unordered": 40+20*f1,
                     "ordered": 60+20*f1, "exact": 100}[grade]
            best = {"grade": grade, "score": score, "precision": precision, "recall": recall,
                    "f1": f1, "matches": count, "actual": len(calls), "expected": len(steps),
                    "reference_index": index, "reference": [[s.model_dump(exclude_none=True) for s in stage]
                                                          for stage in stages],
                    "pairs": [{"step_index": i, "call_index": pos, "call_id": calls[pos]["id"]}
                              for pos, i in sorted(owners.items())],
                    "unmatched_steps": sorted(set(range(len(steps))) - set(owners.values())),
                    "extra_calls": sorted(set(range(len(calls))) - owners.keys())}
            best_key = key
    return best


def assess_trace(case, evidence, checks):
    trace = case.trajectory
    workspace = evidence["workspace"]
    checks["workspace_scope"] = workspace["workspace_scope"]
    if trace.artifact:
        checks["artifact"] = workspace["artifact"]
    writes = (c.get("workspace_path", c["args"].get("path", "")) for c in evidence["calls"]
              if c["name"] in ("edit", "write"))
    checks["allowed_actions"] = (
        not any(c["name"] in trace.forbidden_tools for c in evidence["calls"])
        and all(trace.artifact and isinstance(path, str) and str(PurePosixPath(path)) == trace.artifact.path
                for path in writes))
    done = all(checks.values())
    result = score_trajectory(trace, evidence["calls"], done)
    status = "review" if result["grade"] == "review" else "pass" if done else "fail"
    return {"end_to_end": done, "trajectory": result, "equivalence": trace.equivalence,
            "boundary": trace.boundary, "status": status}
