"""Read bounded, selected evidence and prepare the model-visible material."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from time import monotonic

from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.storage.cache_cleanup import registrations, validate_cleanup_database
from naiyue_test_agent.storage.cache_cleanup_files import CleanupIssue
from naiyue_test_agent.storage.conversation_sql import history_projection
from naiyue_test_agent.storage.maintenance import check_restore_marker

from .batch import digest, encode, read_json, selected_rows, write_json
from .history import PAGE_READ_BUDGET_SECONDS, _event_keys, _reader, _reply_context, source_projects

MESSAGE_LIMIT = 24
TEXT_LIMIT = 1600
MATERIAL_LIMIT = 48_000
KNOWN_SECRET = re.compile(
    r'(?im)^\s*(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key)\s*[:=].*$'
    r'|(?i:\b(?:bearer|basic)\s+[\w.+/=-]+)'
    r'|(?i:["\']?\b(?:[a-z0-9_-]*(?:api[-_]?key|token|password|passwd|secret|credential|cookie|authorization))'
    r'["\']?\s*[:=]\s*(?:"(?:\\.|[^"\\])*"|\'[^\']*\'|[^\s,;}]+))'
    r'|-----BEGIN [^-\r\n]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-\r\n]*PRIVATE KEY-----|\Z)'
    r'|\b(?:sk-|gh[pousr]_|github_pat_|AKIA|ASIA|xox[baprs]-)[A-Za-z0-9_=-]{8,}'
)
SECRET = re.compile(KNOWN_SECRET.pattern + r'|\b[A-Za-z0-9_+=/-]{32,}\b')
QUOTED_PATH = re.compile(r"""(["'])([A-Za-z]:[\\/][^"']+|/[^"']+|~/[^"']+)\1""")
PRIVATE_URL = re.compile(r'(?i)(?<![A-Za-z0-9_])(?:https?|ssh|s3|file)://[^\s"\'<>，。！？；]+')
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
ABSOLUTE = re.compile(r'(?<![A-Za-z0-9_:/])(?:[A-Za-z]:[\\/]|\\\\|~/|/(?!/))[^\s"\'<>,，。！？；]+')
EXTENSION = re.compile(r'\.[A-Za-z0-9]{1,10}$')
FILE_TOOLS = {"read", "ls", "find", "grep", "edit", "write"}
SAFE_TOOL = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
SENSITIVE_FILE = re.compile(
    r"(?i)(?:^|/)(?:[.]env(?:[.]|$)|[.]ssh/|[.]aws/|id_(?:rsa|ed25519)(?:[.]|$)"
    r"|(?:[^/]*[-_.])?(?:credentials?|secrets?)[.](?:json|toml|ya?ml|txt)$)|[.](?:pem|key|p12|pfx)$"
)
SAFE_RELATIVE = re.compile(r"^[\w.-]+(?:/[\w.-]+)*$", re.UNICODE)


class Redactor:
    def __init__(self):
        self.paths = {}

    def path(self, value):
        value = value.replace("\\", "/")
        if (value == "." or (SAFE_RELATIVE.fullmatch(value) and not SECRET.search(value)
                             and all(part not in (".", "..") and not part.startswith(".")
                                     for part in value.split("/")))):
            return value
        ext = EXTENSION.search(value)
        return self.paths.setdefault(value, f"workspace-item-{len(self.paths) + 1}{ext.group() if ext else ''}")

    def text(self, value):
        value = SECRET.sub("[credential removed]", value)
        value = PRIVATE_URL.sub("[URL removed]", value)
        value = EMAIL.sub("[email removed]", value)
        value = QUOTED_PATH.sub(lambda match: match[1] + self.path(match[2]) + match[1], value)
        return ABSOLUTE.sub(lambda match: self.path(match.group()), value)


def _call_args(connection, session, thread, message):
    # Read only scalar path/paging fields. Omit command strings, headers and free-form arguments.
    sql = """SELECT json_extract(c.value,'$.id'),json_extract(c.value,'$.name'),
                  json_extract(c.value,'$.args.path'),json_extract(c.value,'$.args.offset'),
                  json_extract(c.value,'$.args.limit')
             FROM naiyue_chat_messages m, json_each(m.body_json,'$.data.tool_calls') c
             WHERE m.session_id=? AND m.thread_id=? AND m.checkpoint_ns='' AND m.message_id=?
             LIMIT 33"""
    return connection.execute(sql, (session, thread, message)).fetchall()


def _messages(connection, session, context, redactor):
    thread, upper = context["thread_id"], context["response_sequence"]
    history_projection.configure_connection(connection)
    rows = history_projection.page(connection, session, thread, upper=upper, before=upper+1,
                                   count=MESSAGE_LIMIT + 1, chars=TEXT_LIMIT)
    messages = []
    call_ids = {}
    current_calls = {}
    call_number = 0
    visible_reads = set()
    gaps = ["earlier_messages_omitted"] if len(rows) > MESSAGE_LIMIT else []
    for row in reversed(rows[:MESSAGE_LIMIT]):
        message = {"ref": f"M{len(messages)+1}", "role": row["message_type"]}
        truncated = row["text_chars"] > TEXT_LIMIT
        message.update(text=None if truncated else redactor.text(row["content"]), truncated=truncated)
        if row["message_type"] == "ai":
            calls = _call_args(connection, session, thread, row["message_id"])
            if len(calls) > 32:
                gaps.append("tool_calls_omitted")
            message["calls"] = []
            for call_id, name, path, offset, limit in calls[:32]:
                if not isinstance(call_id, str) or not isinstance(name, str) or not SAFE_TOOL.fullmatch(name):
                    gaps.append("invalid_call_projection")
                    continue
                args = {}
                if name not in ("read", "ls"):
                    gaps.append("tool_arguments_omitted")
                if name in FILE_TOOLS:
                    if isinstance(path, str) and 0 < len(path) <= TEXT_LIMIT:
                        args["path"] = redactor.path(path)
                    args.update({key: value for key, value in (("offset", offset), ("limit", limit))
                                 if type(value) is int and value >= 0})
                call_number += 1
                ref = f"T{call_number}"
                key = (row["message_id"], call_id)
                call_ids[key] = None if key in call_ids else ref
                if row["reply_id"] == context["reply_id"]:
                    current_calls[call_id] = None if call_id in current_calls else ref
                message["calls"].append({"ref": ref, "name": name, "args": args})
                if name == "read" and "path" in args and not SENSITIVE_FILE.search(path.replace("\\", "/")):
                    visible_reads.add(ref)
        if row["message_type"] == "tool":
            message["call_ref"] = call_ids.get((row["parent_message_id"], row["tool_call_id"]))
            message["status"] = row["tool_status"] if row["tool_status"] in ("success", "error") else None
            # Tool output may contain arbitrary environment dumps or file trees. Preserve result status
            # and a redacted read result only. The fixture still requires independent confirmation.
            if message["call_ref"] not in visible_reads:
                message["text"] = None
                gaps.append("tool_result_text_omitted")
        messages.append(message)
    return messages, {call: ref for call, ref in current_calls.items() if ref is not None}, gaps


def read_evidence(project, row):
    path = Path(project["database_path"])
    check_restore_marker(path)
    identity = path.stat()
    _, session, run = row["key"]
    frozen = row["user_context"]
    if not frozen:
        return None, ["reply_boundary_unavailable"]
    with _reader(path) as connection:
        deadline = monotonic() + PAGE_READ_BUDGET_SECONDS
        connection.set_progress_handler(lambda: monotonic() >= deadline, 1000)
        connection.execute("BEGIN")
        version = validate_cleanup_database(connection, project["project_id"])
        head = connection.execute("""SELECT last_sequence,capture_state FROM naiyue_diagnostic_runs
              WHERE session_id=? AND run_id=?""", (session, run)).fetchone()
        if head is None or tuple(head) != (row["diagnostic_sequence"], row["diagnostic_capture"]):
            return None, ["diagnostic_changed_or_expired"]
        events = [json.loads(event[0]) for event in connection.execute(
            """SELECT body_json FROM naiyue_diagnostic_events
               WHERE session_id=? AND run_id=? ORDER BY sequence""", (session, run))]
        _, ai_ids, _ = _event_keys(events)
        _, current = _reply_context(connection, version, session, ai_ids)
        if not current or any(current.get(key) != value for key, value in frozen.items()):
            return None, ["reply_boundary_changed"]
        messages, call_ids, gaps = _messages(connection, session, current, Redactor())
        if any(message["truncated"] for message in messages):
            gaps.append("oversized_message_omitted")
    check_restore_marker(path)
    after = path.stat()
    if (identity.st_dev, identity.st_ino) != (after.st_dev, after.st_ino):
        return None, ["project_database_replaced"]

    outcomes = []
    for tool in row["tools"]:
        name = tool["name"]
        if not name or not SAFE_TOOL.fullmatch(name):
            continue
        ref = call_ids.get(tool["call_id"]) if tool["scope"] == "root" else None
        outcomes.append({"ref": f"D{len(outcomes)+1}", "call_ref": ref, "scope": tool["scope"],
                         "name": name, "outcome": tool["outcome"]})
    if any(tool["scope"] == "child" for tool in outcomes):
        gaps.append("child_outcomes_only")
    if row["diagnostic_incomplete"]:
        gaps.append("diagnostic_incomplete")
    evidence = {"messages": messages, "observations": outcomes, "run_status": row["status"]}
    if len(encode(evidence)) > MATERIAL_LIMIT:
        return None, [*gaps, "material_size_limit"]
    return evidence, gaps


def extract_batch(batch, data_dir):
    manifest = read_json(batch / "manifest.json")
    registry, projects = source_projects(data_dir, batch)
    rows = selected_rows(manifest)
    manifest_digest = digest(manifest)
    existing = {}
    material_file = batch / "materials.json"
    if material_file.exists() or material_file.is_symlink():
        previous = read_json(material_file)
        if previous["manifest_digest"] != manifest_digest:
            raise ValueError("materials do not belong to the current manifest")
        existing = {tuple(item["source"]): item for item in previous["candidates"] if item["state"] == "ready"}
    candidates = []
    for row in rows:
        if tuple(row["key"]) in existing:
            candidates.append(existing[tuple(row["key"])])
            continue
        project_id, _, _ = row["key"]
        candidate = {"id": digest(row["key"])[:24], "source": row["key"]}
        project = projects.get(project_id)
        frozen_project = manifest["projects"].get(project_id, {})
        evidence, gaps = None, ["registration_changed"]
        if (project and project["lifecycle"] == "active" and
                frozen_project.get("registration_revision", project["revision"]) == project["revision"]):
            try:
                evidence, gaps = read_evidence(project, row)
                with _reader(registry) as connection:
                    current = registrations(connection, 1, project_id)
                if current != [(project_id, project["database_path"], project["lifecycle"], project["revision"])]:
                    evidence, gaps = None, ["registration_changed"]
            except (CleanupIssue, ProjectError, sqlite3.Error, OSError, ValueError, KeyError, TypeError) as error:
                evidence = None
                gaps = [getattr(error, "code", None) or getattr(error, "sqlite_errorname", None) or type(error).__name__]
        candidates.append(dict(candidate, state="ready" if evidence else "needs_material",
                               gaps=sorted(set(gaps)), evidence=evidence))
    document = {"schema_version": 1, "manifest_digest": manifest_digest,
                "extracted_at": datetime.now(timezone.utc).isoformat(), "candidates": candidates}
    write_json(batch / "materials.json", document)
    return document
