"""Run with the separate evaluation environment, which includes Inspect AI."""
import json
from pathlib import Path

from langchain_core.messages import AIMessage

from evals.__main__ import main
from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel


def test_real_application_and_inspect_report(tmp_path, monkeypatch):
    def comparison(answer):
        return [AIMessage(content="", tool_calls=[
            {"id": "old", "name": "read", "args": {"path": "before.json"}},
            {"id": "new", "name": "read", "args": {"path": "after.json"}},
        ]), AIMessage(content=answer)]

    replies = [AIMessage(content="测试输入边界附近的值。")]
    replies.extend(comparison("T-17\nT-18"))
    for _ in range(4):
        replies.append(AIMessage(content="请告诉我两份报告的文件名？"))
        replies.extend(comparison("T-17"))
    actor = StreamingToolModel(replies=replies)

    def catalog(_):
        judge = StreamingToolModel(replies=[AIMessage(content='{"score":2,"reason":"fixture verdict"}')])
        return ModelCatalog("actor", {"actor": actor, "judge": judge})

    monkeypatch.setattr(application, "load_model_catalog", catalog)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    config = tmp_path / "models.json"
    config.write_text("{}")
    exit_code = main(["run", "--config", str(config), "--model", "actor", "--judge-model", "judge",
                      "--output", str(tmp_path)])
    assert exit_code == 1
    report = next(tmp_path.glob("run-*/results.json"))
    data = json.loads(report.read_text())
    assert data["summary"]["pass_4"] == {"clarify-reports": True}
    assert data["summary"]["groups"]["session"]["first_text_samples"] == 5
    assert data["summary"]["groups"]["session"]["latency_samples"] == 5
    compare = next(row for row in data["attempts"] if row["case_id"] == "compare-reports")
    assert compare["checks"] == {"required_reads": True, "final_ids": False}
    multi = [row for row in data["attempts"] if row["case_id"] == "clarify-reports"]
    assert all(len(row["evidence"]["turn_seconds"]) == 2 for row in multi)
    assert (report.parent / "report.html").exists()
    assert list((report.parent / "inspect").glob("*.eval"))


def test_real_run_scores_trajectory_and_file_result_separately(tmp_path, monkeypatch):
    from shutil import copytree
    dataset_root = Path("evals/data")
    copytree(dataset_root / "trace-project", tmp_path / "trace-project")
    row = next(json.loads(line) for line in (dataset_root / "trajectory.jsonl").read_text().splitlines()
               if json.loads(line)["id"] == "trace-code-edit")
    dataset = tmp_path / "subset.jsonl"
    second = dict(row, id="wrong-file-result")
    dataset.write_text("\n".join(json.dumps(r) for r in (row, second)))
    replies = []
    for value in [4, 8]:
        for name, args in [("read", {"path": "retry.py"}), ("edit", {"path": "retry.py", "edits": [
                {"oldText": "RETRY_LIMIT = 3", "newText": f"RETRY_LIMIT = {value}"}]})]:
            replies.append(AIMessage(content="", tool_calls=[{"name": name, "id": f"c-{name}-{value}", "args": args}]))
        replies.append(AIMessage(content="已修改。"))
    actor = StreamingToolModel(replies=replies)
    monkeypatch.setattr(application, "load_model_catalog", lambda _: ModelCatalog("actor", {"actor": actor}))
    config = tmp_path / "models.json"
    config.write_text("{}")
    before = (tmp_path/"trace-project"/"retry.py").read_bytes()
    assert main(["run", "--dataset", str(dataset), "--config", str(config), "--model", "actor",
                 "--output", str(tmp_path)]) == 1
    report = json.loads(next(tmp_path.glob("run-*/results.json")).read_text())
    assert report["manifest"]["judge_model"] is None
    assert [(r["trajectory"]["grade"], r["end_to_end"], r["trajectory"]["recall"])
            for r in report["attempts"]] == [("exact", True, 1), ("partial", False, 1)]
    assert (tmp_path/"trace-project"/"retry.py").read_bytes() == before


def test_real_collaboration_distinguishes_child_read_from_parent_answer(tmp_path, monkeypatch):
    from shutil import copytree
    from test_async_application import call

    dataset_root = Path("evals/data")
    copytree(dataset_root / "collaboration-project", tmp_path / "collaboration-project")
    first = json.loads((dataset_root / "collaboration.jsonl").read_text().splitlines()[0])
    dataset = tmp_path / "collaboration.jsonl"
    dataset.write_text("\n".join(json.dumps(dict(first, id=name)) for name in ("verified", "guessed")))
    delegate = {"agent": "failure-investigator", "task": "读取 checkout-report.txt 并返回证据编号"}
    model = StreamingToolModel(replies=[
        call("subagent", delegate, "outer-1"),
        call("read", {"path": "checkout-report.txt"}, "child-read"),
        AIMessage(content="FAIL-Q72"),
        AIMessage(content="FAIL-Q72"),
        call("subagent", delegate, "outer-2"),
        AIMessage(content="FAIL-Q72"),
        AIMessage(content="FAIL-Q72"),
    ])
    monkeypatch.setattr(application, "load_model_catalog", lambda _: ModelCatalog("actor", {"actor": model}))
    config = tmp_path / "models.json"
    config.write_text("{}")
    assert main(["run", "--dataset", str(dataset), "--config", str(config), "--model", "actor",
                 "--output", str(tmp_path)]) == 1
    report = json.loads(next(tmp_path.glob("run-*/results.json")).read_text())
    verified, guessed = report["attempts"]
    assert verified["status"] == "pass" and all(verified["collaboration"].values())
    assert guessed["status"] == "fail" and guessed["end_to_end"]
    assert guessed["collaboration"] == {
        "routing": True, "handoff": True, "child_execution": False, "child_report": True}
    child, = verified["evidence"]["children"]
    assert child["parent_call_id"] == "outer-1"
    assert child["calls"][0]["id"] == "child-read"


def test_open_collaboration_cli_scores_semantics_from_real_child_evidence(tmp_path, monkeypatch):
    from shutil import copytree
    from langchain_core.messages import HumanMessage
    from test_async_application import call

    root = Path("evals/data")
    copytree(root / "collaboration-project", tmp_path / "collaboration-project")
    raw = json.loads((root / "collaboration-open.jsonl").read_text().splitlines()[0])
    dataset = tmp_path / "open.jsonl"
    dataset.write_text(json.dumps(raw))
    config = tmp_path / "models.json"
    config.write_text("{}")
    command = ["run", "--config", str(config), "--model", "actor", "--dataset", str(dataset),
               "--output", str(tmp_path)]
    import pytest
    with pytest.raises(SystemExit) as error:
        main(command)
    assert error.value.code == 2
    actor = StreamingToolModel(replies=[
        call("subagent", {"agent": "test-designer", "task": "读取 retry-policy.txt 并设计用例"}, "delegate"),
        call("read", {"path": "retry-policy.txt"}, "read-policy"),
        AIMessage(content="子报告包含测试序列"),
        AIMessage(content="父交付遗漏耗尽重试场景"),
    ])
    verdict = {name: {"score": score, "reason": "固定裁判用于验证接线"}
               for name, score in (("handoff", 2), ("child_report", 2), ("final_delivery", 1))}
    judge = StreamingToolModel(replies=[AIMessage(content=json.dumps(verdict))])
    monkeypatch.setattr(application, "load_model_catalog", lambda _: ModelCatalog("actor", {"actor": actor, "judge": judge}))
    assert main(command + ["--judge-model", "judge"]) == 1
    row, = json.loads(next(tmp_path.glob("run-*/results.json")).read_text())["attempts"]
    assert all(row["collaboration"].values())
    assert row["rubric"] == verdict and row["status"] == "fail" and not row["end_to_end"]
    prompt = next(m for m in judge.seen[0] if isinstance(m, HumanMessage))
    evidence = json.loads(prompt.text)
    assert evidence["child_runs"][0]["calls"][0]["id"] == "read-policy"
    assert evidence["child_runs"][0]["answers"] == ["子报告包含测试序列"]
    assert evidence["final_answer"] == "父交付遗漏耗尽重试场景"


def test_dynamic_dialogue_runs_four_isolated_attempts_and_scores_without_a_judge(tmp_path, monkeypatch):
    from shutil import copytree
    from langchain_core.messages import HumanMessage
    import pytest
    from test_async_application import call

    base = Path("evals/data")
    copytree(base / "dialogue-project", tmp_path / "dialogue-project")
    dataset = tmp_path / "multi-turn.jsonl"
    dataset.write_text((base / "multi-turn.jsonl").read_text().splitlines()[0])
    config = tmp_path / "models.json"
    config.write_text("{}")
    command = ["run", "--config", str(config), "--model", "actor", "--dataset", str(dataset),
               "--output", str(tmp_path)]
    with pytest.raises(SystemExit) as error:
        main(command)
    assert error.value.code == 2

    actor_replies, simulator_replies = [], []
    for attempt in range(1, 5):
        actor_replies.extend([
            AIMessage(content="两个相对路径是什么？"),
            call("read", {"path": "before.json"}, "read-before"),
            call("read", {"path": "after.json"}, "read-after"),
            AIMessage(content="T-17" if attempt < 4 else "T-19"),
        ])
        simulator_replies.extend([
            AIMessage(content=json.dumps({"action": "reply",
                                         "message": f"第 {attempt} 次用 before.json 和 after.json。"})),
            AIMessage(content='{"action":"stop","message":""}'),
        ])
    actor = StreamingToolModel(replies=actor_replies)
    simulator = StreamingToolModel(replies=simulator_replies)
    monkeypatch.setattr(application, "load_model_catalog",
                        lambda _: ModelCatalog("actor", {"actor": actor, "simulator": simulator}))
    assert main(command + ["--user-model", "simulator"]) == 1
    result = json.loads(next(tmp_path.glob("run-*/results.json")).read_text())
    assert result["summary"]["pass_4"] == {"dynamic-clarify-reports": False}
    assert result["summary"]["simulated_dialogue"]["pass_4_rate"] == 0
    assert result["manifest"]["judge_model"] is None
    for attempt, row in enumerate(result["attempts"], 1):
        transcript = row["evidence"]["dialogue"]
        assert len(transcript) == 2 and transcript[1]["user"].startswith(f"第 {attempt} 次")
        assert row["evidence"]["dialogue_stop"] == "user_stop"
        assert row["end_to_end"] == (attempt < 4)
        assert len([m for m in actor.seen[(attempt - 1) * 4] if isinstance(m, HumanMessage)]) == 1
        assert len(row["evidence"]["simulator_seconds"]) == 2
