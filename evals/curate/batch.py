"""Local batch storage shared by material extraction and annotation."""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from naiyue_test_agent.storage.files import acquire_store_lock, open_store_file, release_store_lock


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return sha256(encode(value).encode()).hexdigest()


def read_json(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or unsafe batch file: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    with TemporaryDirectory(dir=path.parent, prefix=".write-") as directory:
        tmp = Path(directory) / "record"
        tmp.write_text(encode(value) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)


@contextmanager
def batch_writer(batch):
    if batch.is_symlink() or not batch.is_dir():
        raise ValueError("expected a local batch directory")
    lock = batch / ".curation.lock"
    if not lock.exists():
        # A mistyped --batch must not create a lock in a project or product data directory.
        selected_rows(read_json(batch / "manifest.json"))
    fd = open_store_file(lock)
    try:
        acquire_store_lock(fd)
        try:
            yield
        finally:
            release_store_lock(fd)
    finally:
        os.close(fd)


def selected_rows(manifest):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("unsupported scan manifest")
    try:
        rows = {tuple(row["key"]): row for row in manifest["index"]}
        selected = [tuple(key) for key in manifest["selected"]]
        if len(set(selected)) != len(selected):
            raise ValueError("duplicate selected run")
        if any(len(key) != 3 or not all(isinstance(part, str) and part for part in key) for key in selected):
            raise ValueError("invalid selected run key")
        return [rows[key] for key in selected]
    except (TypeError, KeyError) as error:
        raise ValueError("invalid scan manifest index or selected runs") from error
