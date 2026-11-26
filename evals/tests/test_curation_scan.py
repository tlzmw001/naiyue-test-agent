from decimal import Decimal

from evals.curate.selection import parse_rules, select


def test_filter_samples_and_keeps_provenance():
    rules = parse_rules({"any": [
        {"all": [{"tool_error": {"name": "read", "outcomes": ["error_result"]}}]},
        {"all": [{"run_status": ["failed"]}]},
    ]})
    rows = [
        {"key": ["p", "s", key], "status": status, "tools": tools, "tools_complete": complete}
        for key, status, tools, complete in [
            ("a", "failed", [{"scope": "root", "name": "read", "outcome": "error_result"}], True),
            ("b", "completed", [], True),
            ("c", "completed", [], False),
            ("d", "failed", [], True),
        ]
    ]
    percentages = {"matched": Decimal(50), "unmatched": Decimal(100)}
    result = select(rows, rules, percentages, seed="42")
    assert result == select(list(reversed(rows)), rules, percentages, seed="42")
    assert result["pools"]["matched"]["population"] == 2
    assert result["pools"]["matched"]["selected"] == 1
    assert result["pools"]["unmatched"]["selected"] == 1
    assert result["pools"]["unknown"]["population"] == 1
    assert result["index"][0]["matching_rules"] == [1, 2]


def test_quota_boundaries_and_round_counts():
    rows = [{"key": ["p", "s", str(n)], "model_calls": n, "models_complete": complete}
            for n, complete in [(0, False), (1, True), (2, False)]]
    rules = parse_rules({"any": [{"all": [{"min_model_calls": 2}]}]})
    result = select(rows, rules, {"matched": Decimal(100), "unmatched": Decimal("25.5")}, seed="42")
    assert result["selected"] == [["p", "s", "2"]]
    assert result["pools"]["unknown"]["population"] == 1
    assert result["pools"]["unmatched"]["selected"] == 0
    assert select(rows, [], {"all": Decimal(0)}, "42")["selected"] == []
    assert len(select(rows, [], {"all": Decimal(100)}, "42")["selected"]) == 3


def test_unconfirmed_tool_outcome_is_not_a_confirmed_non_error():
    row = {"key": ["p", "s", "r"], "tools_complete": True, "tools": [
        {"scope": "root", "name": "read", "outcome": "outcome_unknown"}]}

    def pool(name, outcomes):
        rules = parse_rules({"any": [{"all": [{"tool_error": {"name": name, "outcomes": outcomes}}]}]})
        return select([row], rules, {}, "seed")["index"][0]["pool"]

    assert pool("read", ["exception", "error_result"]) == "unknown"
    assert pool("read", ["outcome_unknown"]) == "matched"
    assert pool("write", ["exception"]) == "unmatched"


def test_archive_pairing_counts_a_reused_id_once_and_omits_ambiguous_outcomes():
    import sqlite3
    import json
    from contextlib import closing
    from evals.curate.history import _archive_tools, _event_keys

    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("""CREATE TABLE naiyue_chat_messages(message_id,parent_message_id,message_type,
            body_json,session_id,thread_id,checkpoint_ns,reply_id,source_thread_id,kind,sequence)""")
        def insert(identity, parent, kind, data, sequence):
            db.execute("INSERT INTO naiyue_chat_messages VALUES(?,?,?,?,'s','t','','r','t','message',?)",
                       (identity, parent, kind, json.dumps({"data": data}), sequence))
        for number, name, outcome in [(1, "read", "success"), (2, "read", "error"), (3, "write", "success")]:
            parent = f"a{number}"
            insert(parent, None, "ai", {"tool_calls": [{"id": "shared", "name": name}]}, number * 2)
            insert(f"t{number}", parent, "tool", {"tool_call_id": "shared", "status": outcome}, number * 2 + 1)
        context = {"thread_id": "t", "reply_id": "r", "end_sequence": 1, "response_sequence": 7}
        for included, expected_name, expected_outcome in [
                (["a1"], "read", "success"), (["a1", "a2"], "read", None),
                (["a1", "a2", "a3"], None, None)]:
            tools = []
            _archive_tools(db, "s", context, included, tools)
            tool, = tools
            assert (tool["call_id"], tool["name"], tool["outcome"]) == ("shared", expected_name, expected_outcome)
        missing_label = {"event_type": "tool_call_started", "source": None,
                         "data": {"tool_call_id": "shared", "fields_omitted": True}}
        def named(name):
            return {**missing_label, "data": {"tool_call_id": "shared", "tool_name": name}}
        for events, expected_name in [([missing_label], "read"), ([named("read"), named("write")], None)]:
            tools, _, _ = _event_keys(events)
            _archive_tools(db, "s", context, ["a1"], tools)
            assert tools[0]["name"] == expected_name


def test_scan_during_live_run_and_stop(tmp_path, monkeypatch):
    import asyncio
    import json
    import sqlite3
    import sys
    from contextlib import closing
    from uuid import uuid4

    from langchain_core.messages import AIMessage

    from evals.curate import history
    from evals.curate.__main__ import main
    from naiyue_test_agent import application
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import create_session_read_tool
    from test_async_application import StreamingToolModel, call

    async def exercise():
        data, workspace = tmp_path / "data", tmp_path / "workspace"
        workspace.mkdir()
        config = tmp_path / "models.json"
        config.write_text("{}")
        model = StreamingToolModel(replies=[call("read", {"path": "missing.txt"}), AIMessage(content="PRIVATE_ANSWER")])
        monkeypatch.setattr(application, "load_model_catalog", lambda _: ModelCatalog("actor", {"actor": model}))
        settings = AppSettings.model_validate({"storage": {"data_dir": str(data)},
                                               "memory": {"enabled": False, "auto_enabled": False}})
        async with open_service_application(config, tools=[create_session_read_tool()],
                                            system_prompt="test", response_format=None, settings=settings,
                                            initial_active_tools=["read"]) as app:
            project = await app.projects.add_local(workspace, trusted=True, operation_id=uuid4().hex)
            host = await app.projects.open(project.project_id)
            session = await host.sessions.create("actor")
            earlier, finished, active = uuid4().hex, uuid4().hex, uuid4().hex
            model.replies.insert(0, AIMessage(content="first reply"))
            await host.runs.start(session, [("user", "first request")], run_id=earlier)
            await host.runs.start(session, [("user", "PRIVATE_REQUEST read /Users/example/private/report.json\nAuthorization: Bearer integration-secret")], run_id=finished)
            model.entered, model.release = asyncio.Event(), asyncio.Event()
            model.seen.clear()
            model.replies.append(AIMessage(content="unused while waiting"))
            task = asyncio.create_task(host.runs.start(session, [("user", "stop me")], run_id=active))
            await asyncio.wait_for(model.entered.wait(), 5)

            options = ["scan", "--data-dir", str(data), "--project", project.project_id,
                       "--from", "2000-01-01T00:00:00Z", "--to", "2100-01-01T00:00:00Z"]
            rules = tmp_path / "rules.json"
            rules.write_text(json.dumps({"any": [{"all": [
                {"tool_error": {"name": "read", "outcomes": ["error_result"]}},
                {"min_user_turns": 2}, {"min_model_calls": 2}, {"min_tool_calls": 1},
            ]}]}))
            batch = tmp_path / "batch"
            # The same CLI runs in its own worker while the product has its project open.
            try:
                assert await asyncio.to_thread(main, [*options, "--rules", str(rules), "--output", str(batch)]) == 0
                report = (batch / "manifest.json").read_text()
                manifest = json.loads(report)
                assert manifest["selected"] == [[project.project_id, session, finished]], manifest
                assert "PRIVATE_" not in report
                records = {row["key"][2]: row for row in manifest["index"]}
                assert set(records) == {earlier, finished}
                assert records[finished]["status"] == "completed"
                assert records[earlier]["user_turns"] == 1
                assert records[finished]["user_turns"] == 2
                materials = (batch / "materials.json").read_text()
                assert "integration-secret" not in materials and "/Users/example" not in materials
                candidate, = json.loads(materials)["candidates"]
                assert candidate["source"] == [project.project_id, session, finished]
                assert candidate["state"] == "ready"
                assert any("workspace-item-1.json" in (message["text"] or "")
                           for message in candidate["evidence"]["messages"])
                assert any(observation["outcome"] == "error_result"
                           for observation in candidate["evidence"]["observations"])
            finally:
                host.runs.stop(session)
                assert (await asyncio.wait_for(task, 5))["run_stopped"]
        second = tmp_path / "after-stop"
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "evals.curate", *options, "--output", str(second),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 0, stderr.decode()
        assert "3/3 selected" in stdout.decode()
        by_id = {row["key"][2]: row for row in json.loads((second / "manifest.json").read_text())["index"]}
        assert by_id[active]["status"] == "stopped"
        assert len(by_id) == 3
        boundary = tmp_path / "boundary"
        assert main([*options, "--from", by_id[finished]["closed_at"], "--to", by_id[active]["closed_at"],
                     "--output", str(boundary)]) == 0
        keys = json.loads((boundary / "manifest.json").read_text())["selected"]
        assert keys == [[project.project_id, session, finished]]

        # The archived response was superseded before an older batch could extract it.
        (boundary / "materials.json").unlink()
        response_id = by_id[finished]["user_context"]["response_message_id"]
        with closing(sqlite3.connect(project.database_path)) as writer:
            writer.execute("UPDATE naiyue_chat_messages SET revision=revision+1 WHERE message_id=?", (response_id,))
            writer.commit()
        assert main(["scan", "--resume", "--data-dir", str(data), "--output", str(boundary)]) == 0
        changed, = json.loads((boundary / "materials.json").read_text())["candidates"]
        assert changed["state"] == "needs_material" and changed["gaps"] == ["reply_boundary_changed"]

        busy = tmp_path / "busy"
        with closing(sqlite3.connect(project.database_path)) as writer:
            assert writer.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
            writer.execute("BEGIN EXCLUSIVE")
            assert main([*options, "--output", str(busy)]) == 2
            issues = json.loads((busy / "manifest.json").read_text())["projects"]
            assert issues[project.project_id]["issue"] == "SQLITE_BUSY"
            writer.rollback()
        with closing(sqlite3.connect(project.database_path)) as writer:
            # A truncated diagnostic stream can lose both tool events while its
            # AI message and saved reply still identify the original request.
            writer.execute("""DELETE FROM naiyue_diagnostic_events WHERE run_id=?
                AND json_extract(body_json,'$.event_type') IN ('tool_call_started','tool_call_finished')""", (finished,))
            writer.execute("""UPDATE naiyue_diagnostic_runs SET
                summary_json=json_set(summary_json,'$.capture_incomplete',json('true')) WHERE run_id=?""", (finished,))
            writer.commit()
        reduced = tmp_path / "partial-trace"
        rules.write_text(json.dumps({"any": [{"all": [{"min_tool_calls": 1}]}]}))
        assert main([*options, "--rules", str(rules), "--output", str(reduced)]) == 0
        partial = json.loads((reduced / "manifest.json").read_text())
        assert partial["selected"] == [[project.project_id, session, finished]]
        observed = next(row for row in partial["index"] if row["key"][2] == finished)
        only_call, = observed["tools"]
        assert only_call["name"] == "read" and only_call["outcome"] is None
        assert not observed["tools_complete"]
        with closing(sqlite3.connect(project.database_path)) as writer:
            writer.execute("DELETE FROM naiyue_diagnostic_events WHERE run_id=?", (finished,))
            writer.execute("DELETE FROM naiyue_diagnostic_runs WHERE run_id=?", (finished,))
            writer.commit()
        usage_only = tmp_path / "usage-only"
        assert main([*options, "--output", str(usage_only)]) == 0
        rows = json.loads((usage_only / "manifest.json").read_text())["index"]
        restored = next(row for row in rows if row["key"][2] == finished)
        assert restored["time_basis"] == "usage_closed" and restored["status"] is None
        frozen = (batch / "manifest.json").read_bytes()
        assert main(["scan", "--resume", "--data-dir", str(data), "--output", str(batch)]) == 0
        assert (batch / "manifest.json").read_bytes() == frozen
        candidate, = json.loads((batch / "materials.json").read_text())["candidates"]
        assert candidate["state"] == "ready"
        (batch / "materials.json").unlink()  # Represent an S1-only batch whose evidence expired.
        assert main(["scan", "--resume", "--data-dir", str(data), "--output", str(batch)]) == 0
        candidate, = json.loads((batch / "materials.json").read_text())["candidates"]
        assert candidate["state"] == "needs_material"
        assert candidate["gaps"] == ["diagnostic_changed_or_expired"]

    monkeypatch.setattr(history, "PAGE_SIZE", 1)
    asyncio.run(exercise())


def test_tool_events_keep_parent_and_child_separate_and_avoid_double_counting():
    from evals.curate.history import _event_keys

    child = {"agent_path": "root/reviewer", "agent_run_id": "child-run"}
    events = [
        {"event_type": "tool_call_started", "source": None, "data": {"tool_call_id": "t", "tool_name": "read"}},
        {"event_type": "tool_call_finished", "source": None,
         "data": {"tool_call_id": "t", "tool_name": "read", "outcome": "success"}},
        {"event_type": "tool_call_finished", "source": child,
         "data": {"tool_call_id": "t", "tool_name": "read", "outcome": "exception"}},
    ]
    tools, _, complete = _event_keys(events)
    assert complete and [(t["scope"], t["outcome"]) for t in tools] == [("root", "success"), ("child", "exception")]
    row = {"key": ["p", "s", "r"], "tools": tools, "tools_complete": complete}
    def rule(scope):
        return parse_rules({"any": [{"all": [{"tool_error": {
            "scope": scope, "name": "read", "outcomes": ["exception"],
        }}]}]})
    assert select([row], rule("root"), {}, "seed")["index"][0]["pool"] == "unmatched"
    assert select([row], rule("child"), {}, "seed")["index"][0]["pool"] == "matched"
