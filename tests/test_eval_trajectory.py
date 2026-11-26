import pytest

from evals.trajectory import TrajectorySpec, score_trajectory


def step(path):
    return {"name": "read", "args": {"path": path}}


def spec(*refs):
    return TrajectorySpec(equivalence="文件问答", boundary="两次读", references=refs)


def calls(paths):
    return [{"name": "read", "args": {"path": path}, "batch": batch, "status": "success",
             "id": str(i), "result": "ok"} for i, (batch, path) in enumerate(paths)]


@pytest.mark.parametrize(("paths", "done", "grade", "p", "r"), [
    ([(1,"a"),(2,"b")], True, "exact", 1, 1),
    ([(1,"a"),(2,"a"),(3,"b")], True, "ordered", 2/3, 1),
    ([(1,"b"),(2,"a")], True, "unordered", 1, 1),
    ([(1,"a"),(1,"b")], True, "unordered", 1, 1),
    ([(1,"a")], False, "partial", 1, .5),
    ([(1,"c")], False, "divergent", 0, 0),
    ([(1,"c")], True, "review", 0, 0),
    ([(1,"a"),(2,"b")], False, "partial", 1, 1),
])
def test_five_bands_and_unlisted_solution(paths, done, grade, p, r):
    s = spec([[step("a")], [step("b")]])
    result = score_trajectory(s, calls(paths), done)
    assert (result["grade"], result["precision"], result["recall"]) == (grade, p, r)
    assert (result["score"] is None) == (grade == "review")


def test_independent_batch_alternative_and_empty():
    s = spec([[step("x")]], [[step("a"), step("b")]])
    assert score_trajectory(s, calls([(1,"b"),(1,"a")]), True)["grade"] == "exact"
    assert score_trajectory(spec([]), [], True)["score"] == 100
    assert score_trajectory(spec([]), calls([(1,"a")]), False)["precision"] == 0
    assert score_trajectory(s, [], False)["recall"] == 0


def test_receipt_arguments_and_multiplicity():
    s = spec([[step("a"), step("a")]])
    actual = calls([(1,"a"),(1,"b")])
    assert score_trajectory(s, actual, False)["matches"] == 1
    for status in [None, "error", "invalid"]:
        actual[0]["status"] = status
        assert score_trajectory(s, actual, False)["matches"] == 0


def test_summary_reports_ungraded_success_separately(tmp_path):
    import json
    from evals.report import write_report
    rows = []
    for name, done, observed in [("normal", True, "a"), ("new-route", True, "b"), ("miss", False, "c")]:
        trace = score_trajectory(spec([[step("a")]]), calls([(1, observed)]), done)
        rows.append(dict(case_id=name, mode="session", attempts=1, attempt=1, end_to_end=done,
                         status="review" if name == "new-route" else "pass" if done else "fail",
                         trajectory=trace, equivalence="文件问答", boundary="单个"))
    write_report(tmp_path, rows, {})
    summary = json.loads((tmp_path/"results.json").read_text())["summary"]["trajectory"]
    assert summary["graded"] == 2 and summary["grades"]["review"] == 1
    assert summary["mean_score"] == 50
    assert "轨迹评分" in (tmp_path/"report.html").read_text()


def test_file_acceptance_and_test_script_mutants(tmp_path):
    from evals.cases import load_cases
    from evals.trajectory_workspace import collect_workspace, snapshot
    from pathlib import Path

    cases = {c.id: c for c in load_cases(Path("evals/data/trajectory.jsonl"))}
    trace = cases["trace-automation"].trajectory
    baseline = snapshot(tmp_path)
    target = tmp_path/trace.artifact.path
    source = ("from eligibility import eligible\n\ndef test_edges():\n"
              "    assert not eligible(99)\n    assert eligible(100) is True\n    assert eligible(101)\n")
    target.write_text(source)
    assert collect_workspace(trace, tmp_path, baseline)["artifact"]
    for wrong in [source.replace("assert eligible(101)", "assert True"),
                  source.replace("not eligible(99)", "eligible(99)"),
                  source.replace("assert eligible(101)", "return True")]:
        target.write_text(wrong)
        assert not collect_workspace(trace, tmp_path, baseline)["artifact"]
    (tmp_path/"unrelated").write_text("change")
    assert not collect_workspace(trace, tmp_path, baseline)["workspace_scope"]


def test_calls_require_a_paired_receipt_in_their_own_model_batch():
    from langchain_core.messages import AIMessage, ToolMessage
    from evals.execution import record_messages

    evidence = {"calls": [], "answers": [], "output_tokens": []}
    req = {"name": "read", "args": {"path": "a"}, "id": "reused"}
    record_messages(evidence, [AIMessage(content="", tool_calls=[req]), AIMessage(content="", tool_calls=[req]),
                               ToolMessage(content="ok", name="read", tool_call_id="reused")], 1)
    assert [c["status"] for c in evidence["calls"]] == [None, "success"]
    assert [c["batch"] for c in evidence["calls"]] == [1, 2]


def test_overlapping_step_patterns_report_an_ordered_alignment():
    generic = {"name": "read", "args": {}}
    s = spec([[generic], [step("a")]])
    actual = calls([(1, "a"), (2, "a")])
    trace = score_trajectory(s, actual, True)
    assert trace["grade"] == "exact"
    assert [pair["step_index"] for pair in trace["pairs"]] == [0, 1]


def test_repeating_one_answer_is_not_a_second_result():
    from evals.cases import load_cases
    from evals.scoring import judge
    from pathlib import Path
    case = next(c for c in load_cases(Path("evals/data/trajectory.jsonl")) if c.id == "trace-search-many")
    assert not judge(case, {"calls": [], "answers": ["W-17\nW-22\nW-22"]})["final_ids"]


def test_unconfirmed_or_wrong_result_never_matches_success():
    s = spec([[{**step("a"), "result_contains": "reference=7"}]])
    actual = calls([(1, "a")])
    assert score_trajectory(s, actual, False)["matches"] == 0
    actual[0]["result"] = "reference=7"
    assert score_trajectory(s, actual, True)["score"] == 100
    actual[0]["not_executed"] = "permission_denied"
    assert score_trajectory(s, actual, False)["matches"] == 0


def test_duplicate_cost_and_not_reusing_a_tool_receipt():
    trace = score_trajectory(spec([[step("a")], [step("b")]]),
                             calls([(1, "a"), (2, "a"), (3, "b")]), True)
    assert trace["score"] == 76
    assert trace["extra_calls"] == [1]
    assert [pair["call_index"] for pair in trace["pairs"]] == [0, 2]
    from evals.execution import record_messages
    from langchain_core.messages import AIMessage, ToolMessage

    req = {"name": "read", "args": {"path": "a"}, "id": "duplicated"}
    evidence = {"calls": [], "answers": [], "output_tokens": []}
    record_messages(evidence, [AIMessage(content="", tool_calls=[req, req]),
                               ToolMessage(content="ok", name="read", tool_call_id="duplicated")], 1)
    assert [c["status"] for c in evidence["calls"]] == [None, None]


def test_automation_accepts_documentation_and_return_none():
    from evals.trajectory_asserts import checks_boundaries
    source = ('"""边界测试。"""\nfrom eligibility import eligible\n\n'
              'def test_edge() -> None:\n    """刚好达到门槛。"""\n    assert eligible(100)\n')
    assert checks_boundaries(source, {"100": True})
