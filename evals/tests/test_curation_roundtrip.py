"""Exercise the offline CLI against product-written history and both outcomes of evals run."""
import asyncio
from hashlib import sha256
import json
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

from evals.__main__ import main as run_evals
from evals.curate.__main__ import main as curate
from evals.curate.review import load_review
from naiyue_test_agent import application
from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import create_session_read_tool
from test_async_application import StreamingToolModel, call


def test_history_to_scored_trajectory(tmp_path, monkeypatch):
    config = tmp_path / "models.json"
    config.write_text("{}", encoding="utf-8")
    data, workspace, batch = [tmp_path / name for name in ("data", "workspace", "batch")]
    workspace.mkdir()
    files = {"draft.txt": "RUN-18\n", "final.txt": "RUN-47\n"}
    for name, contents in files.items():
        (workspace / name).write_text(contents, encoding="utf-8")

    opening = "先记下 draft.txt，等我确认后再读。"
    correction = "更正为 final.txt，请读取并只回复报告编号。"
    source = StreamingToolModel(replies=[AIMessage(content="收到，等你确认。"),
                                         call("read", {"path": "final.txt"}), AIMessage(content="RUN-47")])
    models = {"source": source}
    monkeypatch.setattr(application, "load_model_catalog", lambda _: ModelCatalog("source", models))
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    settings = AppSettings.model_validate({"storage": {"data_dir": str(data)},
                                           "memory": {"enabled": False, "auto_enabled": False}})

    async def record():
        async with open_service_application(config, tools=[create_session_read_tool()], system_prompt="test",
                                            response_format=None, initial_active_tools=["read"], settings=settings) as app:
            project = await app.projects.add_local(workspace, trusted=True, operation_id=uuid4().hex)
            host = await app.projects.open(project.project_id)
            session = await host.sessions.create("source")
            run_id = uuid4().hex
            await host.runs.start(session, [("user", opening)], run_id=uuid4().hex)
            await host.runs.start(session, [("user", correction)], run_id=run_id)
            return [project.project_id, session, run_id]

    key = asyncio.run(record())
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"any": [{"all": [{"min_user_turns": 2}, {"min_tool_calls": 1}]}]}))
    assert curate(["scan", "--data-dir", str(data), "--project", key[0], "--rules", str(rules),
                   "--from", "2000-01-01T00:00:00Z", "--to", "2100-01-01T00:00:00Z",
                   "--output", str(batch), "--seed", "s4"]) == 0
    manifest = json.loads((batch / "manifest.json").read_bytes())
    assert len(manifest["index"]) == 2 and manifest["selected"] == [key]
    candidate, = json.loads((batch / "materials.json").read_bytes())["candidates"]
    evidence = candidate["evidence"]
    correction_ref, = [msg["ref"] for msg in evidence["messages"] if msg["text"] == correction]
    observation, = evidence["observations"]
    assert (observation["scope"], observation["name"], observation["outcome"]) == ("root", "read", "success")

    case = {"id": "corrected-report-path", "mode": "session", "fixture": "reports",
            "turns": [opening, correction], "tools": ["read"], "checks": {"final_ids": ["RUN-47"]},
            "trajectory": {"equivalence": "按用户更正的路径读取报告", "boundary": "两份报告都存在，编号不同",
                           "references": [[[{"name": "read", "args": {"path": "final.txt"}, "status": "success",
                                             "result_contains": "RUN-47"}]]]}}
    proposal = {"direction": "trajectory", "goal": "核对读取路径与最终编号", "missing": [], "case": case,
                "changes": ["保留两轮更正，另行构造两份内容可核对的报告。"], "files": files,
                "evidence": [correction_ref, observation["ref"]]}
    annotator = StreamingToolModel(replies=[AIMessage(content=json.dumps({"proposals": [proposal]}, ensure_ascii=False))])
    models["annotator"] = annotator
    assert curate(["annotate", "--batch", str(batch), "--config", str(config), "--model", "annotator"]) == 0
    request = annotator.seen[0][-1].text
    assert json.loads(request) == {"evidence": evidence, "gaps": candidate["gaps"]}
    assert all(identity not in request for identity in key)

    review = ["review", "--batch", str(batch)]
    assert curate(review) == 0
    draft, = load_review(batch)
    html = (batch / "review.html").read_text(encoding="utf-8")
    assert draft["revision"] in html and candidate["id"] in html
    assert curate([*review, "--candidate", candidate["id"], "--direction", "trajectory",
                   "--revision", draft["revision"], "--decision", "accept"]) == 0
    assert curate(["export", "--batch", str(batch), "--version", "v1"]) == 0
    dataset = batch / "versions" / "v1" / "trajectory.jsonl"
    assert dataset.read_bytes() == draft["line"].encode()
    fixture = dataset.parent / json.loads(dataset.read_bytes())["fixture"]
    assert {p.name: p.read_bytes() for p in fixture.iterdir()} == {k: v.encode() for k, v in files.items()}

    for label, path, answer, expected_exit, grade in (
        ("correct", "final.txt", "RUN-47", 0, "exact"),
        ("stale", "draft.txt", "RUN-18", 1, "divergent"),
    ):
        actor = StreamingToolModel(replies=[AIMessage(content="等你确认。"), call("read", {"path": path}),
                                            AIMessage(content=answer)])
        models[label] = actor
        output = tmp_path / label
        assert run_evals(["run", "--dataset", str(dataset), "--config", str(config), "--model", label,
                          "--output", str(output)]) == expected_exit
        result, = output.glob("run-*/results.json")
        report = json.loads(result.read_bytes())
        row, = report["attempts"]
        assert report["manifest"]["dataset_sha256"] == sha256(dataset.read_bytes()).hexdigest()
        assert row["status"] == ("pass" if label == "correct" else "fail")
        assert row["end_to_end"] is (label == "correct")
        assert row["trajectory"]["grade"] == grade
        assert row["trajectory"]["recall"] == row["trajectory"]["precision"] == int(label == "correct")
        assert len(row["evidence"]["turn_seconds"]) == 2
        assert not any(msg.text == correction for msg in actor.seen[0])
        assert any(msg.text == correction for msg in actor.seen[1])
        executed, = row["evidence"]["calls"]
        assert executed["args"]["path"] == path and executed["status"] == "success"
        assert any(isinstance(msg, ToolMessage) and answer in msg.text for msg in actor.seen[-1])
        assert (result.parent / "report.html").is_file() and list((result.parent / "inspect").glob("*.eval"))
