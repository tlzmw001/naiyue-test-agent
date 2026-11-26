"""Capture task outputs before the isolated project directory is reclaimed."""
import ast
import csv
from hashlib import sha256
import io
import json

from .trajectory_asserts import checks_boundaries


def fixture_files(root):
    return (p for p in root.rglob("*") if p.is_file() and not p.is_symlink()
            and "__pycache__" not in p.relative_to(root).parts and p.suffix != ".pyc")


def snapshot(root):
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def canonical_artifact(kind, content):
    if kind == "python_ast":
        return ast.dump(ast.parse(content))
    if kind == "csv_rows":
        rows = list(csv.reader(io.StringIO(content), strict=True))
        return (rows[:1], sorted(rows[1:]))
    return json.dumps(json.loads(content), sort_keys=True, ensure_ascii=False, allow_nan=False)


def collect_workspace(spec, root, before):
    after = snapshot(root)
    target = spec.artifact
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    result = {"changed_files": changed,
              "workspace_scope": all(target is not None and path == target.path for path in changed)}
    if target:
        result["artifact"] = False
        if target.path in after:
            result["artifact_sha256"] = after[target.path]
            expected = canonical_artifact(target.format, target.content)
            try:
                content = (root / target.path).read_text(encoding="utf-8")
                if target.format == "boundary_asserts":
                    result["artifact"] = checks_boundaries(content, json.loads(expected))
                else:
                    result["artifact"] = canonical_artifact(target.format, content) == expected
            except (SyntaxError, UnicodeError, ValueError, csv.Error, RecursionError) as error:
                result["artifact_error"] = type(error).__name__
    return result
