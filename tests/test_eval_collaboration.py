from pathlib import Path

from evals.cases import load_cases
from evals.collaboration import score_collaboration


def test_parent_answer_cannot_substitute_for_child_execution():
    case = load_cases(Path("evals/data/collaboration.jsonl"))[0]
    assert score_collaboration(case, {"calls": [], "children": []}) == {
        "routing": False, "handoff": False, "child_execution": False, "child_report": False}


def test_handoff_and_child_receipt_are_independently_checked():
    import json

    case = load_cases(Path("evals/data/collaboration.jsonl"))[0]
    receipt = json.dumps({"agent": "failure-investigator", "agent_id": "child",
                          "status": "completed", "report": "FAIL-Q72", "truncated": False})
    call = {"id": "request", "turn": 1, "name": "subagent", "status": "success", "result": receipt,
            "args": {"agent": "failure-investigator", "task": "读取 checkout-report.txt"}}
    child = {"agent_id": "child", "parent_call_id": "request", "status": "completed", "turn": 1,
             "answers": ["FAIL-Q72"], "calls": [{"name": "read", "status": "success",
                         "args": {"path": "checkout-report.txt"}, "result": "FAIL-Q72"}]}
    evidence = {"calls": [call], "children": [child]}
    assert score_collaboration(case, evidence) == {
        "routing": True, "handoff": False, "child_execution": True, "child_report": True}
    call["args"]["task"] += " 并只返回证据编号"
    assert all(score_collaboration(case, evidence).values())
    child["parent_call_id"] = "other-request"
    assert score_collaboration(case, evidence) == {
        "routing": True, "handoff": True, "child_execution": False, "child_report": False}


def test_open_report_is_verified_without_fixed_wording():
    import json

    case = load_cases(Path("evals/data/collaboration-open.jsonl"))[0]
    item = case.collaboration[0]
    report = "具有不同表达方式的子报告"
    receipt = json.dumps({"agent": item.role, "agent_id": "child", "status": "completed", "report": report})
    evidence = {"calls": [{"id": "request", "turn": 1, "name": "subagent", "status": "success",
                           "result": receipt, "args": {"agent": item.role, "task": item.handoff_contains[0]}}],
                "children": [{"agent_id": "child", "parent_call_id": "request", "status": "completed", "turn": 1,
                              "answers": [report], "calls": [{"name": "read", "status": "success",
                              "args": item.read.args, "result": item.read.result_contains}]}]}
    assert all(score_collaboration(case, evidence).values())
    evidence["children"][0]["answers"] = ["另一条回执"]
    assert not score_collaboration(case, evidence)["child_report"]


def test_open_delegation_accepts_parent_material_read_before_handoff():
    import json

    case = load_cases(Path("evals/data/collaboration-open.jsonl"))[0]
    item = case.collaboration[0]
    read = {"name": "read", "status": "success", "args": item.read.args,
            "result": item.read.result_contains, "turn": 1, "batch": 1}
    delegation = {"name": "subagent", "status": "success", "id": "delegated", "turn": 1, "batch": 2,
                  "args": {"agent": item.role, "task": item.handoff_contains[0]},
                  "result": json.dumps(dict(agent=item.role, agent_id="child", status="completed",
                                           report="开放式子报告"))}
    child = {"agent_id": "child", "parent_call_id": "delegated", "turn": 1, "status": "completed",
             "answers": ["开放式子报告"], "calls": []}
    evidence = {"calls": [read, delegation], "children": [child]}
    assert all(score_collaboration(case, evidence).values())
    read["batch"] = delegation["batch"]
    assert not score_collaboration(case, evidence)["child_execution"]
    read["batch"] = 3
    assert not score_collaboration(case, evidence)["child_execution"]
