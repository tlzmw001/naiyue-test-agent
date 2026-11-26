from pathlib import Path

import pytest

from evals.cases import load_cases
from evals.report import percentile, summarize
from evals.scoring import judge
from evals.tool_specs import component_tools


DATASET = Path("evals/data/smoke.jsonl")


def test_first_cases_and_scoring_distinguish_trajectory_from_answer():
    _, compare, multi = load_cases(DATASET)
    evidence = {
        "answers": ["T-17\nT-18"],
        "calls": [
            {"name": "read", "args": {"path": name}, "status": "success"}
            for name in ("after.json", "before.json")
        ],
    }
    scores = judge(compare, evidence)
    assert scores == {"required_reads": True, "final_ids": False}
    assert multi.attempts == 4


def test_nearest_rank_and_pass_all():
    assert percentile(list(range(1, 51)), .95) == 48
    assert percentile(list(range(1, 51)), .99) == 50
    rows = [
        {"case_id": "four", "mode": "session", "attempts": 4, "status": status,
         "agent_seconds": 1.0}
        for status in ("pass", "pass", "pass", "fail")
    ]
    assert summarize(rows)["pass_4"] == {"four": False}
    rows[-1]["status"] = "error"
    assert summarize(rows)["pass_4"] == {"four": None}


def test_reject_escape_fixture(tmp_path):
    data = tmp_path / "cases.jsonl"
    data.write_text('{"id":"x","mode":"session","turns":["x"],"fixture":"..","checks":{"final_ids":[]}}')
    with pytest.raises(ValueError, match="fixture"):
        load_cases(data)


def test_outcome_rate_is_independent_of_trajectory():
    rows = [
        {"case_id": "correct-answer", "mode": "session", "attempts": 1,
         "status": "fail", "end_to_end": True},
        {"case_id": "wrong-answer", "mode": "session", "attempts": 1,
         "status": "fail", "end_to_end": False},
    ]
    system = summarize(rows)["groups"]["session"]
    assert system["assertion_pass_rate"] == 0
    assert system["end_to_end_success_rate"] == .5


def test_subagent_routing_checks_call_contract_and_multiplicity():
    cases = load_cases(Path("evals/data/subagent-routing.jsonl"))
    review, design, both, private, simple = cases[:5]

    def delegation(role, **overrides):
        return {"name": "subagent", "args": {"agent": role, "task": "Review the supplied report.", **overrides}}

    def score(case, *calls):
        return judge(case, {"answers": [""], "calls": calls})

    inspect = delegation("failure-investigator")
    plan = delegation("test-designer")
    assert score(review, inspect) == {"subagent_routes": True}
    assert score(design, inspect) == {"subagent_routes": False}
    assert score(both, plan, inspect) == {"subagent_routes": True}
    assert score(both, plan) == {"subagent_routes": False}
    assert score(both, plan, inspect, inspect) == {"subagent_routes": False}
    assert score(review, delegation("failure-investigator", task=" ")) == {"subagent_routes": False}
    assert score(review, delegation("failure-investigator", agent_id="a" * 64)) == {"subagent_routes": False}
    assert score(private) == {"no_tools": True}
    assert score(simple, plan) == {"no_tools": False}


def test_tool_selection_and_arguments_have_independent_checks():
    cases = load_cases(Path("evals/data/tool-calls.jsonl"))
    read = next(case for case in cases if case.id == "tool-read-page")

    def score(*calls):
        return judge(read, {"answers": [""], "calls": calls})

    expected = {"name": "read", "args": {"path": "logs/checkout.log", "offset": 21, "limit": 10}}
    assert score(expected) == {"tool_selected": True, "tool_arguments": True}
    assert score({**expected, "name": "grep"}) == {"tool_selected": False, "tool_arguments": False}
    assert score({**expected, "args": {**expected["args"], "offset": 20}}) == {
        "tool_selected": True, "tool_arguments": False}
    assert score({**expected, "args": {**expected["args"], "offset": "21"}}) == {
        "tool_selected": True, "tool_arguments": False}
    assert score(expected, expected) == {"tool_selected": False, "tool_arguments": False}


def test_tool_cases_cover_current_first_party_tool_contracts():
    cases = load_cases(Path("evals/data/tool-calls.jsonl"))
    expected = [case.checks.tool_call for case in cases if case.checks.tool_call]
    assert {call.name for call in expected} == component_tools().keys()
    assert any(case.checks.no_tools for case in cases)
    for case in cases:
        call = case.checks.tool_call
        assert all(judge(case, {"answers": [], "calls": [call.model_dump()] if call else []}).values())


def test_planning_diagnostics_classify_wrong_terminal_outcomes():
    edit, _, blocked, _ = load_cases(Path("evals/data/planning.jsonl"))[:4]
    partial = {"answers": ["状态：已完成"], "calls": [], "plan_steps_completed": 1}
    scores = judge(edit, partial)
    assert scores["required_steps"] is False
    assert scores["completion_claim_supported"] is False
    abandoned = {**partial, "answers": ["状态：无法完成"]}
    assert judge(edit, abandoned)["avoidable_abandonment"] is False
    correctly_blocked = {**abandoned, "plan_steps_completed": len(blocked.plan_steps)}
    assert all(judge(blocked, correctly_blocked).values())


def test_planning_never_lets_batch_skip_observations():
    from langchain_core.messages import AIMessage
    from evals.planning import simulate_plan_batch

    edit = load_cases(Path("evals/data/planning.jsonl"))[0]
    calls = [
        {"id": str(n), "name": step.call.name, "args": step.call.args}
        for n, step in enumerate(edit.plan_steps[:2])
    ]
    completed, results = simulate_plan_batch(edit, AIMessage(content="", tool_calls=calls), 0)
    assert completed == 1
    assert [item.status for item in results] == ["success", "error"]
    assert "status=draft" in results[0].content


def test_planner_feeds_selected_tool_results_back_to_model():
    import asyncio

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from evals.execution import execute_plan

    case = load_cases(Path("evals/data/planning.jsonl"))[1]

    class Model:
        calls = 0

        async def ainvoke(self, messages):
            position = self.calls
            self.calls += 1
            if position:
                last = messages[-1]
                previous = case.plan_steps[position - 1]
                assert isinstance(last, ToolMessage)
                assert last.content == previous.result
                assert last.status == previous.status
                assert last.tool_call_id == str(position)
            if position == len(case.plan_steps):
                return AIMessage(content="状态：已完成\n已确定修改后为 ready。")
            expected = case.plan_steps[position].call
            return AIMessage(content="", tool_calls=[
                {"id": str(position + 1), "name": expected.name, "args": expected.args}
            ])

    evidence = {"answers": [], "calls": [], "turn_seconds": [], "output_tokens": []}
    asyncio.run(execute_plan(case, Model(), [HumanMessage(content=case.turns[0])], evidence))
    assert all(judge(case, evidence).values())
    assert [call["status"] for call in evidence["calls"]] == [
        "success", "error", "success", "success", "success"]


def test_readonly_plan_grades_returned_value():
    case = next(c for c in load_cases(Path("evals/data/planning.jsonl")) if c.id == "plan-read-only-review")
    evidence = {"answers": ["状态：已完成\nstatus=ready"], "calls": [], "plan_steps_completed": 1}
    assert not all(judge(case, evidence).values())
    evidence["answers"] = ["状态：已完成\nstatus=draft"]
    assert all(judge(case, evidence).values())


def test_goal_accepts_alternative_edits_and_rejects_collateral_changes():
    import asyncio
    from langchain_core.messages import AIMessage
    from evals.execution import execute_plan

    cases = {c.id: c for c in load_cases(Path("evals/data/planning.jsonl"))}

    def run(case, requests):
        replies = [AIMessage(content="", tool_calls=[{"name": name, "args": {"path": "config.txt", **args},
                                                       "id": str(n)}]) for n, (name, args) in enumerate(requests)]
        replies.append(AIMessage(content="状态：已完成"))
        class Model:
            async def ainvoke(self, messages):
                return replies.pop(0)
        evidence = {"answers": [], "calls": [], "turn_seconds": [], "output_tokens": []}
        asyncio.run(execute_plan(case, Model(), [], evidence))
        return judge(case, evidence)

    change = cases["plan-goal-change"]
    noop = cases["plan-goal-already-ready"]
    read = ("read", {})
    edit = ("edit", {"edits": [{"oldText": "draft", "newText": "ready"}]})
    write = ("write", {"content": change.file_goal.expected})
    assert all(run(change, [read, edit]).values())
    assert all(run(change, [read, write, read]).values())
    assert not run(change, [read, ("write", {"content": "status=ready\n"})])["file_content"]
    assert not run(change, [read])["completion_claim_supported"]
    assert all(run(noop, [read]).values())
    assert not run(noop, [read, write])["constraints_respected"]
    assert not run(noop, [])["required_reads"]
    assert not all(run(noop, [("read", {"limit": 1})]).values())
    assert all(run(noop, [("read", {"path": "./config.txt", "offset": 2, "limit": 1})]).values())
    assert not run(noop, [("read", {"path": "../config.txt"}), read])["constraints_respected"]


def test_description_swap_changes_expected_route_and_preserves_user_request():
    cases = load_cases(Path("evals/data/subagent-routing.jsonl"))[-2:]
    first, swapped = cases
    assert first.turns == swapped.turns
    assert [r.name for r in first.roles] == [r.name for r in swapped.roles]
    assert first.roles[0].description == swapped.roles[1].description
    call = {"name": "subagent", "args": {"agent": "review-a", "task": "排查回归失败"}}
    evidence = {"answers": [""], "calls": [call]}
    assert judge(first, evidence)["subagent_routes"]
    assert not judge(swapped, evidence)["subagent_routes"]


def test_first_line_requires_offset_one_or_its_effective_default():
    case = next(c for c in load_cases(Path("evals/data/tool-calls.jsonl")) if c.id == "tool-read-single-line")
    for offset, expected in [({}, True), ({"offset": None}, True), ({"offset": 1}, True), ({"offset": 2}, False)]:
        call = {"name": "read", "args": {"path": "report.txt", "limit": 1, **offset}}
        assert judge(case, {"calls": [call], "answers": []})["tool_arguments"] is expected


def test_read_page_ignores_unrelated_context():
    cases = {c.id: c for c in load_cases(Path("evals/data/tool-calls.jsonl"))}
    original, variant = cases["tool-read-page"], cases["tool-read-page-background"]
    assert original.turns != variant.turns
    assert original.tools == variant.tools and original.checks == variant.checks
