"""Assemble portable dataset versions in an isolated staging directory."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from evals.cases import load_cases

from .annotation import DATASETS, _safe_path
from .batch import digest, encode, read_json, write_json
from .review import BASELINE, decision_key, signature


def _files(root):
    inventory = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("dataset version contains a symbolic link")
        if path.is_file():
            inventory[path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()
        elif not path.is_dir():
            raise ValueError("dataset version contains a nonregular file")
    return inventory


def _entries(root, *, origin):
    entries = []
    for direction in sorted(DATASETS):
        dataset = root / (direction + ".jsonl")
        if not dataset.exists():
            continue
        if dataset.is_symlink():
            raise ValueError("unsafe dataset path")
        cases = load_cases(dataset)
        lines = [line for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
        for case, line in zip(cases, lines):
            raw = json.loads(line)
            files = {}
            if case.fixture:
                files = {relative: (case.fixture / relative).read_bytes().decode()
                         for relative in _files(case.fixture)}
            entries.append({"direction": direction, "case": raw, "files": files,
                            "line": encode(raw) + "\n", "origin": origin})
    return entries


def read_version(path):
    if path.is_symlink() or not path.is_dir():
        raise ValueError("expected a published dataset version directory")
    inventory = _files(path)
    inventory.pop("version.json", None)
    meta = read_json(path / "version.json")
    if meta.get("schema_version") != 1 or meta.get("files") != inventory:
        raise ValueError("dataset version fingerprint mismatch")
    details = {key: value for key, value in meta.items() if key != "version_digest"}
    if digest(details) != meta.get("version_digest"):
        raise ValueError("dataset version manifest digest mismatch")
    entries = _entries(path, origin=meta["version"])
    expected = {direction + ".jsonl" for direction in {entry["direction"] for entry in entries}}
    expected.update(entry["case"]["fixture"] + "/" + name
                    for entry in entries for name in entry["files"])
    for entry in entries:
        fixture = entry["case"].get("fixture")
        if fixture:
            _safe_path(fixture)
            if fixture != f"fixtures/{entry['direction']}/{digest(entry['case']['id'])[:24]}":
                raise ValueError("unexpected fixture layout in published version")
    counts = dict(Counter(entry["direction"] + ".jsonl" for entry in entries))
    if set(inventory) != expected or not entries or meta.get("datasets") != counts:
        raise ValueError("unexpected file inventory in published version")
    return meta, entries


def load_baseline():
    return _entries(BASELINE, origin="冷启动基线")


def _merge(drafts, decisions, prior, baseline):
    by_id = {(item["direction"], item["case"]["id"]): signature(item) for item in [*baseline, *prior]}
    known_content = set(by_id.values())
    output = list(prior)
    added = duplicates = 0
    current = {decision_key(draft): draft for draft in drafts if draft["direction"]}
    for key, choice in sorted(decisions.items()):
        if choice["decision"] != "accept":
            continue
        entry = current.get(key)
        if not entry or entry["revision"] != choice["revision"] or entry["state"] != "ready":
            raise ValueError(f"accepted draft changed or is unavailable: {key}")
        identity = (entry["direction"], entry["case"]["id"])
        payload = signature(entry)
        if identity in by_id and by_id[identity] != payload:
            raise ValueError(f"case ID has different contents: {identity[0]}/{identity[1]}")
        if payload in known_content:
            duplicates += 1
            continue
        output.append(entry)
        by_id[identity] = payload
        known_content.add(payload)
        added += 1
    if not output:
        raise ValueError("no approved cases to export")
    return output, {"added": added, "retained": len(prior), "duplicates": duplicates}


def publish(batch, name, drafts, decisions, previous=None):
    if len(_safe_path(name).parts) != 1:
        raise ValueError("version must be a single directory name")
    parent, prior = read_version(previous) if previous else (None, [])
    output, summary = _merge(drafts, decisions, prior, load_baseline())
    versions = batch / "versions"
    if versions.is_symlink():
        raise ValueError("unsafe versions directory")
    versions.mkdir(exist_ok=True, mode=0o700)
    target = versions / name
    if previous and previous.resolve() == target.resolve():
        raise ValueError("choose a new version name")
    by_direction = {}
    for entry in sorted(output, key=lambda row: (row["direction"], row["case"]["id"])):
        by_direction.setdefault(entry["direction"], []).append(entry)
    with TemporaryDirectory(dir=versions, prefix=".publish-") as temporary:
        staging = Path(temporary) / "dataset"
        staging.mkdir(mode=0o700)
        for direction, entries in by_direction.items():
            dataset = staging / (direction + ".jsonl")
            dataset.write_bytes("".join(entry["line"] for entry in entries).encode())
            for entry in entries:
                for name_in_fixture, contents in entry["files"].items():
                    file = staging / _safe_path(entry["case"]["fixture"]) / _safe_path(name_in_fixture)
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(contents.encode())
            load_cases(dataset)
        details = {"schema_version": 1, "version": name, "parent": parent["version_digest"] if parent else None,
                   "datasets": {direction + ".jsonl": len(entries) for direction, entries in by_direction.items()},
                   "files": _files(staging)}
        document = dict(details, version_digest=digest(details))
        write_json(staging / "version.json", document)
        if target.exists() or target.is_symlink():
            existing, _ = read_version(target)
            if existing != document:
                raise ValueError("version name already contains different data")
        else:
            staging.rename(target)
    return target, summary, by_direction
