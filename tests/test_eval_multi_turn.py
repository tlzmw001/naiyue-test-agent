from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from native_application_helpers import async_test

from evals.cases import load_cases
from evals.multi_turn import follow_up


@async_test
async def test_simulator_receives_hidden_goal_and_live_reply_but_no_answer_key():
    case = load_cases(Path("evals/data/multi-turn.jsonl"))[0]
    evidence = {"dialogue": [{"user": case.turns[0], "assistant": "请给我两份报告的文件名。"}]}

    class Sim:
        async def ainvoke(self, messages):
            payload = messages[-1].text
            assert "请给我两份报告的文件名。" in payload
            assert case.simulation.goal in payload
            assert case.checks.final_ids[0] not in payload
            return AIMessage(content='{"action":"reply","message":"用 before.json 和 after.json。"}')

    reply = await follow_up(Sim(), case, evidence)
    assert reply.message == "用 before.json 和 after.json。"


@async_test
async def test_early_stop_from_simulator_is_a_protocol_error():
    case = load_cases(Path("evals/data/multi-turn.jsonl"))[0]

    class Sim:
        async def ainvoke(self, messages):
            return AIMessage(content='{"action":"stop","message":""}')

    with pytest.raises(ValueError, match="before two"):
        await follow_up(Sim(), case, {"dialogue": [{"user": "开始", "assistant": "回复"}]})


def test_simulator_error_leaves_four_attempt_result_unresolved():
    from evals.report import summarize

    rows = [{"case_id": "dynamic", "mode": "session", "attempts": 4, "simulated_dialogue": True,
             "status": status} for status in ("pass", "pass", "error", "pass")]
    summary = summarize(rows)
    assert summary["pass_4"]["dynamic"] is None
    assert summary["simulated_dialogue"] == {
        "cases": 1, "graded_cases": 0, "passed_cases": 0, "pass_4_rate": None}


def test_first_turn_accepts_a_polite_material_request_without_question_mark():
    from evals.scoring import judge

    case = load_cases(Path("evals/data/multi-turn.jsonl"))[0]
    evidence = {"answers": ["请提供旧报告和新报告的文件名或路径。", "T-17"], "calls": []}
    assert judge(case, evidence)["clarify_first"]
    evidence["calls"].append({"name": "read", "args": {"path": "before.json"}, "turn": 1})
    assert not judge(case, evidence)["clarify_first"]
    evidence["calls"].clear()
    evidence["answers"][0] = "我还没拿到文件名。"
    assert not judge(case, evidence)["clarify_first"]
    evidence["answers"][0] = "你今天过得好吗？"
    assert not judge(case, evidence)["clarify_first"]
