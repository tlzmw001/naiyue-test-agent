from evals.curate.material import Redactor
from evals.curate.annotation import validate_proposals


def test_redaction_preserves_path_identity_and_scrubs_credentials():
    clean = Redactor()
    visible = clean.text("读取 /Users/example/private/report.json。\nAuthorization: Bearer sample-inline-token")
    assert "sample-inline-token" not in visible and "/Users/example" not in visible
    path = clean.path("/Users/example/private/report.json")
    assert path in visible and path.endswith(".json")
    assert clean.path("/Users/example/private/other.json") != path
    assert "actual-password" not in clean.text('{"password":"actual-password","status":"failed"}')


def test_repeated_call_id_cannot_authorize_a_different_read_result(monkeypatch):
    from evals.curate import material

    def row(kind, message_id, content, parent=None):
        return {"message_type": kind, "message_id": message_id, "parent_message_id": parent,
                "reply_id": "reply", "text_chars": len(content), "content": content,
                "tool_call_id": "shared", "tool_status": "success"}

    records = [row("ai", "first", ""), row("tool", "first-result", "public fact", "first"),
               row("ai", "second", ""), row("tool", "second-result", "private contents", "second")]
    monkeypatch.setattr(material.history_projection, "configure_connection", lambda _: None)
    monkeypatch.setattr(material.history_projection, "page", lambda *args, **kwargs: records[::-1])
    monkeypatch.setattr(material, "_call_args", lambda _, session, thread, message:
                        [("shared", "read", "report.txt" if message == "first" else ".env", None, None)])
    messages, current, gaps = material._messages(None, "session",
        {"thread_id": "thread", "response_sequence": 4, "reply_id": "reply"}, Redactor())
    assert messages[1]["text"] == "public fact" and messages[3]["text"] is None
    assert messages[1]["call_ref"] != messages[3]["call_ref"]
    assert "shared" not in current and "tool_result_text_omitted" in gaps


def test_tool_case_is_loadable_and_missing_multiturn_material_is_explicit():
    directions = validate_proposals({"proposals": [
        {"direction": "tool-calls", "goal": "checks use of corrected report", "evidence": ["M1"],
         "changes": ["made first tool explicit"], "missing": [], "files": {},
         "case": {"id": "corrected", "mode": "model", "turns": ["请先读取 corrected.json"],
                  "tools": ["read"], "checks": {"tool_call": {"name": "read", "args": {"path": "corrected.json"}}}}},
        {"direction": "multi-turn", "goal": "checks later correction", "evidence": ["M1"],
         "changes": [], "missing": ["缺少原始报告"], "files": {}, "case": None},
    ]}, {"M1"})
    assert [item["status"] for item in directions] == ["draft", "needs_material"]
    assert directions[0]["case"]["checks"]["tool_call"]["args"]["path"] == "corrected.json"


def test_annotation_uses_only_cleaned_input_and_retries_failed_candidate(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from evals.curate.__main__ import main
    from evals.curate.batch import digest, write_json
    from naiyue_test_agent import application

    sources = [["private-project", "private-session", run] for run in ("r1", "r2", "r3")]
    manifest = {"schema_version": 1, "index": [{"key": key} for key in sources], "selected": sources}
    write_json(tmp_path / "manifest.json", manifest)
    candidates = [{"id": digest(source)[:24], "source": source, "state": "ready", "gaps": [],
                   "evidence": {"messages": [{"ref": "M1", "text": f"更正文件为 report-{number}.json"}]}}
                  for number, source in enumerate(sources, 1)]
    candidates[2].update(state="needs_material", gaps=["reply_boundary_changed"], evidence=None)
    write_json(tmp_path / "materials.json", {"manifest_digest": digest(manifest), "candidates": candidates})
    config = tmp_path / "models.json"
    config.write_text("{}")
    seen = []
    class Catalog:
        def prepare_async_clients(self):
            pass
        def get(self, model_id):
            assert model_id == "annotator"
            return self
        async def aclose(self):
            pass
        async def ainvoke(self, messages):
            request = messages[-1].content
            seen.append(request)
            assert "private-" not in request
            if "report-1" in request and len(seen) == 1:
                raise TimeoutError("private backend details")
            return SimpleNamespace(text=json.dumps({"proposals": []}))

    monkeypatch.setattr(application, "load_model_catalog", lambda _: Catalog())
    args = ["annotate", "--batch", str(tmp_path), "--config", str(config), "--model", "annotator"]
    assert main(args) == 2
    def result(index):
        return json.loads((tmp_path / "annotations" / (candidates[index]["id"] + ".json")).read_text())
    assert result(0)["error"] == "TimeoutError"
    assert result(1)["state"] == "annotated"
    assert result(2)["state"] == "needs_material"
    assert main(args) == 0
    assert len(seen) == 3 and result(0)["state"] == "annotated"


def test_unsafe_fixture_path_is_rejected():
    import pytest

    case = {"id": "topic", "mode": "session", "fixture": "sandbox", "turns": ["Explain the role"],
            "checks": {"rubric": "The reply names the agent's role"}}
    proposal = {"direction": "system-prompt", "goal": "system prompt", "evidence": ["M1"],
                "changes": ["fabricated fixture"], "missing": [], "files": {"../outside.txt": "text"}, "case": case}
    with pytest.raises(ValueError, match="unsafe fixture path"):
        validate_proposals({"proposals": [proposal]}, {"M1"})


def test_manual_fixture_draft_can_be_imported_without_a_model(tmp_path, monkeypatch):
    import json

    from evals.curate.__main__ import main
    from evals.curate.batch import digest, write_json
    from naiyue_test_agent import application

    def unexpected(*_):
        raise AssertionError("manual notes must not use the model catalog")

    monkeypatch.setattr(application, "load_model_catalog", unexpected)
    source = ["p", "s", "r"]
    candidate_id = digest(source)[:24]
    manifest = {"schema_version": 1, "index": [{"key": source}], "selected": [source]}
    write_json(tmp_path / "manifest.json", manifest)
    candidate = {"id": candidate_id, "source": source, "state": "needs_material",
                 "evidence": None, "gaps": ["diagnostic_changed_or_expired"]}
    write_json(tmp_path / "materials.json", {"manifest_digest": digest(manifest), "candidates": [candidate]})
    draft = tmp_path / "manual.json"
    write_json(draft, {"proposals": [{
        "direction": "system-prompt", "goal": "是否知道可见文件", "evidence": ["manual"],
        "changes": ["为评测人工构造 notes.txt"], "missing": [], "files": {"notes.txt": "synthetic"},
        "case": {"id": "manual-context", "mode": "session", "fixture": "workspace", "turns": ["如何使用 notes.txt？"],
                 "checks": {"rubric": "明确说明可读取项目中的 notes.txt"}},
    }]})
    assert main(["annotate", "--batch", str(tmp_path), "--candidate", candidate_id, "--draft", str(draft)]) == 0
    saved = json.loads((tmp_path / "annotations" / (candidate_id + ".json")).read_text())
    assert saved["method"]["kind"] == "manual"
    proposal, = saved["proposals"]
    assert proposal["files"] == {"notes.txt": "synthetic"}
    assert proposal["requires"] == {"judge_model": True, "user_model": False}


def test_redaction_recognizes_locations_adjacent_to_chinese_text():
    clean = Redactor()
    unix = "/Users/example/private/report.txt"
    windows = r"C:\Users\example\report.txt"
    assert clean.text(f"读取{unix}。") == f"读取{clean.path(unix)}。"
    assert clean.text(f"读取{windows}。") == f"读取{clean.path(windows)}。"
    assert clean.text("访问https://internal.invalid/report?key=value。") == "访问[URL removed]。"
    assert clean.text("读取src/models/report.py。") == "读取src/models/report.py。"
