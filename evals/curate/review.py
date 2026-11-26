"""Render local drafts and bind decisions to the content the reviewer saw."""
from difflib import SequenceMatcher
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory

from evals.cases import EvalCase
from evals.collaboration import score_collaboration
from evals.scoring import judge
from evals.trajectory import assess_trace

from .annotation import DATASETS, Proposal, _input, evidence_refs, load_candidates, validate_proposals
from .batch import digest, encode, read_json, write_json
from .material import KNOWN_SECRET

BASELINE = Path(__file__).resolve().parents[1] / "data"
DECISIONS = ("accept", "needs-material", "link", "discard")


def dataset_entry(direction, case, files):
    case = dict(case)
    if case.get("fixture"):
        case["fixture"] = f"fixtures/{direction}/{digest(case['id'])[:24]}"
    return {"direction": direction, "case": case, "files": files, "line": encode(case) + "\n"}


def signature(entry):
    case = {key: value for key, value in entry["case"].items() if key not in ("id", "fixture")}
    canonical = EvalCase.model_validate(case | {"id": "placeholder"}).model_dump(mode="json")
    return digest([entry["direction"], canonical, entry["files"]])


def _publication_issue(entry, candidate):
    case = entry["case"]
    private = [case["checks"].get("rubric"), (case.get("simulation") or {}).get("goal")]
    sources = {source for source in candidate["source"] if len(source) >= 8}
    if KNOWN_SECRET.search(entry["line"]) or any(source in entry["line"] for source in sources):
        return "用例含疑似凭证或来源标识"
    visible = [*case["turns"], *(text for _, text in case.get("history", []))]
    for role in case.get("roles", []):
        visible.extend((role["name"], role["description"]))
    if any(value in text for value in private if value for text in visible):
        return "被测 Agent 的输入含评分细则或模拟用户目标"
    for path, text in entry["files"].items():
        if (KNOWN_SECRET.search(path) or KNOWN_SECRET.search(text)
                or any(value in path or value in text for value in private if value)
                or any(source in text or source in path for source in sources)):
            return "fixture 含疑似凭证、评分材料或来源标识"
    return None


def load_review(batch):
    directory = batch / "annotations"
    if directory.is_symlink():
        raise ValueError("unsafe annotations directory")
    drafts = []
    for candidate in load_candidates(batch):
        identity = candidate["id"]
        path = directory / (identity + ".json")
        if not path.exists() and not path.is_symlink():
            drafts.append({"candidate_id": identity, "direction": None, "state": candidate["state"],
                           "candidate": candidate})
            continue
        record = read_json(path)
        if record["candidate_id"] != identity:
            raise ValueError("annotation belongs to another candidate")
        needs_refresh = record["material_digest"] != digest(_input(candidate))
        proposals = record["proposals"]
        if not proposals or needs_refresh:
            drafts.append({"candidate_id": identity, "direction": None, "state":
                           "needs_refresh" if needs_refresh else record["state"], "candidate": candidate})
            continue
        refs = evidence_refs(candidate)
        if record["method"]["kind"] == "manual":
            refs.add("manual")
        validated = validate_proposals({"proposals": [
            {key: value for key, value in proposal.items() if key in Proposal.model_fields}
            for proposal in proposals
        ]}, refs)
        for proposal in validated:
            direction = proposal["direction"]
            entry = dataset_entry(direction, proposal["case"], proposal["files"]) if proposal["case"] else None
            revision = digest({"material": record["material_digest"], "proposal": proposal, "entry": entry})
            issue = _publication_issue(entry, candidate) if entry else None
            drafts.append({**(entry or {"line": None}), "candidate_id": identity, "direction": direction,
                           "proposal": proposal, "revision": revision, "issue": issue, "candidate": candidate,
                           "state": "ready" if proposal["status"] == "draft" and not issue else "needs_material"})
    return drafts


def load_decisions(batch):
    path = batch / "decisions.json"
    return read_json(path) if path.exists() or path.is_symlink() else {}


def decision_key(draft):
    return draft["candidate_id"] + "/" + draft["direction"]


def _check_example(case, evidence):
    if not isinstance(evidence, dict) or type(evidence.get("completed")) is not bool:
        raise ValueError("example requires a boolean completed field")
    checks = judge(case, evidence) if evidence["completed"] else {"completed": False}
    if evidence["completed"] and case.collaboration is not None:
        checks.update(score_collaboration(case, evidence))
    if evidence["completed"] and case.trajectory:
        result = assess_trace(case, evidence, checks)
        if result["status"] == "review":
            raise ValueError("trajectory example needs review and cannot verify a pass or fail")
    if not checks:
        raise ValueError("this case has no deterministic checks for examples")
    return checks


def _verify_examples(draft, examples):
    case = EvalCase.model_validate(draft["case"])
    try:
        if set(examples) != {"positive", "negative"}:
            raise ValueError("examples require positive and negative")
        checks = {name: _check_example(case, evidence) for name, evidence in examples.items()}
        if (not all(checks["positive"].values()) or all(checks["negative"].values())
                or checks["negative"].get("completed") is False):
            raise ValueError("examples must show a pass and a completed run that fails an assertion")
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise ValueError("example evidence does not match the scorer's input") from error
    return checks


def save_decision(batch, drafts, *, candidate, direction, revision, decision, existing, known, examples):
    draft = next((item for item in drafts if item["candidate_id"] == candidate and item["direction"] == direction), None)
    if not draft or draft["revision"] != revision:
        raise ValueError("draft revision changed; regenerate review.html")
    if decision == "accept" and draft["state"] != "ready":
        raise ValueError(draft["issue"] or "draft requires more material")
    if decision == "link" and existing not in {item["case"]["id"] for item in known if item["direction"] == direction}:
        raise ValueError("linked case ID must exist in the same direction")
    if (decision != "link" and existing is not None) or (examples is not None and decision != "accept"):
        raise ValueError("existing case IDs are for link; examples are for accept")
    checks = _verify_examples(draft, examples) if examples is not None else None
    decisions = load_decisions(batch)
    decisions[decision_key(draft)] = {"revision": revision, "decision": decision,
                                      "existing": existing, "example_checks": checks}
    write_json(batch / "decisions.json", decisions)
    return decisions


def _related(draft, known):
    if not draft.get("case"):
        return []
    case = draft["case"]
    query = " ".join(case["turns"]).casefold()
    scored = []
    for entry in known:
        if entry["direction"] != draft["direction"]:
            continue
        target = " ".join(entry["case"]["turns"]).casefold()
        rank = SequenceMatcher(None, query[:2000], target[:2000]).ratio()
        if rank >= 0.45 or case["id"] == entry["case"]["id"]:
            scored.append((rank, entry["case"]["id"], entry.get("origin", "上一版本")))
    return sorted(scored, reverse=True)[:3]


def _pre(value):
    return f"<pre><code>{escape(value if isinstance(value, str) else encode(value))}</code></pre>"


def render_review(batch, drafts, decisions, known):
    sections = []
    for draft in drafts:
        candidate_id = draft["candidate_id"]
        direction = draft["direction"]
        if not direction:
            sections.append(f"<article><h2>{escape(candidate_id)}</h2><p>{escape(draft['state'])}</p>"
                            + _pre(draft["candidate"]["gaps"]) + "</article>")
            continue
        decision = decisions.get(decision_key(draft), {})
        current = decision.get("revision") == draft["revision"]
        state = (decision["decision"] if current else "批准已失效" if decision else draft["state"])
        proposal = draft["proposal"]
        case = draft.get("case")
        parts = [f"<article><h2>{escape(direction)} · {escape(candidate_id)}</h2>",
                 f"<p>{escape(proposal['goal'])}</p><p>{escape(state)} · 版本 <code>{draft['revision']}</code></p>"]
        if draft["issue"]:
            parts.append(f"<p>{escape(draft['issue'])}</p>")
        parts.extend(["<h3>改写和待补材料</h3>", _pre({"changes": proposal["changes"], "missing": proposal["missing"]}),
                      "<details><summary>支持这个方向的历史证据</summary>",
                      _pre({"refs": proposal["evidence"], "material": draft["candidate"]["evidence"]}), "</details>"])
        if case:
            parts.extend(["<h3>Agent 可见输入</h3>", _pre({"history": case.get("history", []), "turns": case["turns"],
                                                          "tools": case.get("tools", []), "roles": case.get("roles", [])}),
                          "<h3>判分和模拟用户</h3>", _pre({key: case[key] for key in
                                  ("checks", "simulation", "trajectory", "collaboration", "plan_steps", "file_goal")
                                  if key in case}), _pre(proposal["requires"]),
                          "<h3>正式 JSONL 行</h3>", _pre(draft["line"])])
            for path, text in sorted(draft["files"].items()):
                parts.extend([f"<h4>{escape(case['fixture'] + '/' + path)}</h4>", _pre(text)])
            parts.append("<h3>验证记录</h3>")
            parts.append(_pre({"结构校验": draft["state"] == "ready",
                               "正反例": decision.get("example_checks") if current else None,
                               "真实运行结果": None}))
        related = _related(draft, known)
        if related:
            parts.append("<h3>相似用例</h3>")
            parts.append(_pre([{"id": identity, "origin": origin} for _, identity, origin in related]))
        sections.append("".join(parts) + "</article>")
    report = """<!doctype html><html lang="zh"><meta charset="utf-8"><title>评测草稿审核</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>
body{margin:40px auto;max-width:1000px;padding:0 24px;font:16px/1.6 system-ui;color:#222}
article{padding:24px 0;border-top:1px solid #ccd2db} pre{background:#f3f5f8;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere}
code{overflow-wrap:anywhere} h2,h3{margin-bottom:8px} summary{cursor:pointer}
</style><h1>评测草稿审核</h1>
<p>使用 review 命令提交候选 ID、方向、内容版本和审核决定。</p>
""" + ("".join(sections) or "<p>本批次尚无候选。</p>") + "</html>"
    with TemporaryDirectory(dir=batch, prefix=".review-") as temporary:
        path = Path(temporary) / "review.html"
        path.write_bytes(report.encode())
        path.chmod(0o600)
        path.replace(batch / "review.html")
    return batch / "review.html"
