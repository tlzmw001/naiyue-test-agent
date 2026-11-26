import json

import pytest

from evals.curate.__main__ import main
from evals.curate.batch import digest, write_json
from evals.curate.review import load_review


def make_batch(root, *, name="case-1", fixture=False):
    root.mkdir()
    source = ["private-project", "private-session", "r1"]
    candidate = digest(source)[:24]
    manifest = {"schema_version": 1, "index": [{"key": source}], "selected": [source]}
    write_json(root / "manifest.json", manifest)
    write_json(root / "materials.json", {"manifest_digest": digest(manifest), "candidates": [
        {"id": candidate, "source": source, "state": "needs_material", "evidence": None, "gaps": []},
    ]})
    case = {"id": name, "mode": "model", "turns": ["请读 new.json"], "tools": ["read"],
            "checks": {"tool_call": {"name": "read", "args": {"path": "new.json"}}}}
    direction, files = "tool-calls", {}
    if fixture:
        direction, files = "system-prompt", {"notes.txt": "fixture text <script>"}
        case.update(mode="session", fixture="workspace", checks={"rubric": "说明如何使用可见材料"})
    draft = root / "manual.json"
    write_json(draft, {"proposals": [{"direction": direction, "goal": "<script>input</script>",
                                    "evidence": ["manual"], "changes": ["人工补题"],
                                    "missing": [], "files": files, "case": case}]})
    assert main(["annotate", "--batch", str(root), "--candidate", candidate, "--draft", str(draft)]) == 0
    return candidate, direction


def approve(root, candidate, direction, revision):
    return main(["review", "--batch", str(root), "--candidate", candidate, "--direction", direction,
                 "--revision", revision, "--decision", "accept"])


def test_preview_export_identity_and_stale_approval(tmp_path):
    root = tmp_path / "batch"
    candidate, direction = make_batch(root, name="long-legitimate-eval-case-with-fixture-and-unique-name", fixture=True)
    assert main(["review", "--batch", str(root)]) == 0
    html = (root / "review.html").read_text()
    assert "&lt;script&gt;input&lt;/script&gt;" in html and "<script>input" not in html
    draft, = load_review(root)
    assert approve(root, candidate, direction, draft["revision"]) == 0
    export = ["export", "--batch", str(root), "--version", "v1"]
    assert main(export) == 0
    assert main(export) == 0
    output = root / "versions" / "v1"
    dataset = output / f"{direction}.jsonl"
    assert dataset.read_text() == draft["line"]
    case = json.loads(draft["line"])
    assert (output / case["fixture"] / "notes.txt").read_text() == "fixture text <script>"
    assert "private-session" not in (output / "version.json").read_text()
    path = root / "annotations" / (candidate + ".json")
    annotation = json.loads(path.read_text())
    annotation["proposals"][0]["files"]["notes.txt"] = "changed"
    write_json(path, annotation)
    with pytest.raises(SystemExit) as error:
        main(["export", "--batch", str(root), "--version", "v2"])
    assert error.value.code == 2
    assert not (root / "versions" / "v2").exists()
    assert dataset.read_text() == draft["line"]


def test_code_score_examples_link_and_private_fixture(tmp_path):
    root = tmp_path / "batch"
    candidate, direction = make_batch(root)
    draft, = load_review(root)
    options = ["review", "--batch", str(root), "--candidate", candidate, "--direction", direction,
               "--revision", draft["revision"]]
    assert main([*options, "--decision", "link", "--existing", "tool-read-page"]) == 0
    assert "tool-read-page" in (root / "decisions.json").read_text()

    def evidence(path):
        return {"completed": True, "answers": [""], "calls": [{"name": "read", "args": {"path": path}}]}

    examples = root / "examples.json"
    write_json(examples, {"positive": evidence("new.json"), "negative": evidence("new.json")})
    with pytest.raises(SystemExit):
        main([*options, "--decision", "accept", "--examples", str(examples)])
    assert "tool-read-page" in (root / "decisions.json").read_text()
    write_json(examples, {"positive": evidence("new.json"), "negative": evidence("old.json")})
    assert main([*options, "--decision", "accept", "--examples", str(examples)]) == 0
    decision, = json.loads((root / "decisions.json").read_text()).values()
    assert decision["example_checks"]["negative"] == {"tool_arguments": False, "tool_selected": True}

    protected = tmp_path / "protected"
    identity, kind = make_batch(protected, fixture=True)
    annotation_path = protected / "annotations" / (identity + ".json")
    annotation = json.loads(annotation_path.read_text())
    proposal = annotation["proposals"][0]
    proposal["files"]["notes.txt"] = proposal["case"]["checks"]["rubric"]
    write_json(annotation_path, annotation)
    leaking, = load_review(protected)
    assert leaking["state"] == "needs_material"
    with pytest.raises(SystemExit):
        approve(protected, identity, kind, leaking["revision"])


def test_incremental_export_deduplicates_and_rejects_conflicting_versions(tmp_path):
    first = tmp_path / "first"
    candidate, direction = make_batch(first)
    initial, = load_review(first)
    assert approve(first, candidate, direction, initial["revision"]) == 0
    assert main(["export", "--batch", str(first), "--version", "v1"]) == 0
    previous = first / "versions" / "v1"

    batch = tmp_path / "second"
    identity, kind = make_batch(batch, name="same-case-renamed")
    draft, = load_review(batch)
    assert approve(batch, identity, kind, draft["revision"]) == 0
    command = ["export", "--batch", str(batch), "--previous", str(previous)]
    assert main([*command, "--version", "v2"]) == 0
    version = batch / "versions" / "v2"
    dataset = version / "tool-calls.jsonl"
    assert dataset.read_text() == initial["line"]
    assert json.loads((version / "version.json").read_text())["parent"]

    annotation_path = batch / "annotations" / (identity + ".json")
    annotation = json.loads(annotation_path.read_text())
    annotation["proposals"][0]["case"]["turns"] = ["一次额外的测试用例"]
    write_json(annotation_path, annotation)
    changed, = load_review(batch)
    assert approve(batch, identity, kind, changed["revision"]) == 0
    with pytest.raises(SystemExit):
        main([*command, "--version", "v2"])
    assert dataset.read_text() == initial["line"]
    assert main([*command, "--version", "v3"]) == 0
    added = batch / "versions" / "v3" / "tool-calls.jsonl"
    assert set(added.read_text().splitlines()) == {changed["line"].strip(), initial["line"].strip()}


def test_version_failure_and_concurrent_writer_leave_no_partial_output(tmp_path, monkeypatch):
    from pathlib import Path
    from evals.curate.batch import batch_writer

    root = tmp_path / "batch"
    identity, direction = make_batch(root)
    draft, = load_review(root)
    assert approve(root, identity, direction, draft["revision"]) == 0
    command = ["export", "--batch", str(root), "--version", "v1"]
    original = Path.rename

    def interrupt(path, target):
        if target.name == "v1":
            raise OSError("synthetic publication interruption")
        return original(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", interrupt)
        with pytest.raises(SystemExit):
            main(command)
        assert not (root / "versions" / "v1").exists()
    with batch_writer(root):
        with pytest.raises(SystemExit):
            main(command)
    assert main(command) == 0


def test_trace_example_requires_a_decisive_trajectory_result(tmp_path):
    root = tmp_path / "batch"
    candidate, _ = make_batch(root, fixture=True)
    path = root / "manual.json"
    raw = json.loads(path.read_text())
    proposal, = raw["proposals"]
    proposal.update(direction="trajectory", files={"service.txt": "run 42"})
    proposal["case"].update(checks={"final_ids": ["42"]}, trajectory={
        "equivalence": "文件问答", "boundary": "单结果",
        "references": [[[{"name": "read", "args": {"path": "service.txt"}}]]],
    })
    write_json(path, raw)
    assert main(["annotate", "--batch", str(root), "--candidate", candidate, "--draft", str(path)]) == 0
    draft, = load_review(root)
    command = ["review", "--batch", str(root), "--candidate", candidate, "--direction", "trajectory",
               "--revision", draft["revision"], "--decision", "accept", "--examples", str(root / "examples.json")]
    guess = {"completed": True, "answers": ["42"], "calls": [], "workspace": {"workspace_scope": True}}
    wrong = dict(guess, answers=["17"])
    write_json(root / "examples.json", {"positive": guess, "negative": wrong})
    with pytest.raises(SystemExit):
        main(command)
    assert not (root / "decisions.json").exists()
    read = {"name": "read", "args": {"path": "service.txt"}, "id": "first", "status": "success", "batch": 1}
    write_json(root / "examples.json", {"positive": dict(guess, calls=[read]), "negative": wrong})
    assert main(command) == 0


@pytest.mark.parametrize("leak", ["fixture_filename", "role_description"])
def test_visible_material_blocks_secret_filename_and_judge_text(tmp_path, leak):
    root = tmp_path / "batch"
    candidate, direction = make_batch(root, fixture=True)
    draft_file = root / "manual.json"
    raw = json.loads(draft_file.read_text())
    proposal, = raw["proposals"]
    if leak == "fixture_filename":
        proposal["files"] = {"notes/sk-testvalue1234.txt": "ordinary text"}
    else:
        case = proposal["case"]
        case.update(mode="model", tools=["subagent"], roles=[{"name": "tester", "description": case["checks"]["rubric"]}])
        del case["fixture"]
        proposal["files"] = {}
    write_json(draft_file, raw)
    assert main(["annotate", "--batch", str(root), "--candidate", candidate, "--draft", str(draft_file)]) == 0
    draft, = load_review(root)
    assert draft["state"] == "needs_material"
    with pytest.raises(SystemExit):
        approve(root, candidate, direction, draft["revision"])
