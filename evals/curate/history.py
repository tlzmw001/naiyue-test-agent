"""Collect bounded, metadata-only views of committed project history."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from time import monotonic

from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.storage.cache_cleanup import read_connection, registrations, validate_cleanup_database
from naiyue_test_agent.storage.cache_cleanup_files import CleanupIssue
from naiyue_test_agent.storage.maintenance import check_restore_marker

from .selection import OUTCOMES, STATUSES

PAGE_SIZE = 64
BUSY_TIMEOUT_MS = 100
PAGE_READ_BUDGET_SECONDS = 1


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    return None


@contextmanager
def _reader(path):
    with read_connection(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA query_only=ON")
        yield connection


def _registry(connection):
    registrations(connection, 0)
    return {row["project_id"]: dict(row) for row in connection.execute(
        "SELECT project_id, database_path, root, lifecycle, revision FROM projects ORDER BY project_id")}


def _event_keys(events):
    tools, ai_messages = {}, []
    complete = True
    for event in events:
        kind, data, source = event["event_type"], event["data"], event["source"]
        if kind in ("tool_call_started", "tool_call_finished"):
            if data.get("fields_omitted"):
                complete = False
            if not data.get("tool_call_id"):
                complete = False
                continue
            key = (source["agent_path"], source["agent_run_id"], data["tool_call_id"]) if source else (
                "root", None, data["tool_call_id"])
            tool = tools.setdefault(key, {"scope": "child" if source else "root",
                                          "agent_run_id": key[1], "call_id": key[2],
                                          "name": None, "outcome": None})
            for field, value in (("name", data.get("tool_name")), ("outcome", data.get("outcome"))):
                if value and field not in tool.get("conflicting_fields", ()):
                    if tool[field] not in (None, value):
                        complete = False
                        tool[field] = None
                        tool.setdefault("conflicting_fields", []).append(field)
                    else:
                        tool[field] = value
        elif (kind in ("message_completed", "message_revised") and source is None
              and data.get("message_type") == "ai" and data.get("message_id")):
            ai_messages.append(data["message_id"])
    for tool in tools.values():
        if tool["outcome"] not in OUTCOMES:
            tool["outcome"] = None
    return list(tools.values()), ai_messages, complete


def _reply_context(connection, version, session_id, ai_messages):
    # One observed AI message must still identify its original reply boundary.
    if version < 12:
        return 0, None
    for message_id in reversed(dict.fromkeys(ai_messages)):
        anchors = connection.execute("""SELECT m.thread_id,m.revision,b.input_sequence,b.input_message_id,m.sequence,b.reply_id
             FROM naiyue_chat_messages m JOIN naiyue_reply_boundaries b
               ON b.session_id=m.session_id AND b.thread_id=m.thread_id AND b.reply_id=m.reply_id
             WHERE m.session_id=? AND m.message_id=? AND m.checkpoint_ns=''
               AND m.source_thread_id=m.thread_id AND b.source_thread_id=b.thread_id LIMIT 2""",
                                     (session_id, message_id)).fetchall()
        if len(anchors) != 1:
            continue
        thread_id, revision, upper, input_id, response_sequence, reply_id = anchors[0]
        count, first, anchor = connection.execute("""SELECT COUNT(*),MIN(sequence),
                SUM(CASE WHEN sequence=? AND message_id=? THEN 1 ELSE 0 END)
            FROM naiyue_chat_messages WHERE session_id=? AND thread_id=? AND checkpoint_ns=''
                AND kind='message' AND message_type='human' AND sequence<=?
                AND COALESCE(json_extract(body_json,'$.data.additional_kwargs.naiyue_image_carrier'),0)!=1""",
                                                 (upper, input_id, session_id, thread_id, upper)).fetchone()
        if anchor == 1:
            return count, {"thread_id": thread_id, "revision": revision, "first_sequence": first,
                           "end_sequence": upper, "anchor_message_id": input_id,
                           "response_sequence": response_sequence, "response_message_id": message_id, "reply_id": reply_id}
    return 0, None


def _archive_tools(connection, session, context, ai_messages, tools):
    # A reply can span several runs. Only a root AI message observed in this
    # run can supply a missing request; its receipt must also name that parent.
    rows = connection.execute("""WITH scope(session_id,thread_id,reply_id,low,high) AS (VALUES(?,?,?,?,?)),
    requests AS (
        SELECT m.message_id,json_extract(c.value,'$.id') AS call_id,json_extract(c.value,'$.name') AS name
        FROM naiyue_chat_messages m JOIN scope USING(session_id,thread_id,reply_id),
             json_each(m.body_json,'$.data.tool_calls') c
        WHERE m.message_type='ai' AND m.checkpoint_ns='' AND m.kind='message'
          AND m.source_thread_id=m.thread_id AND m.sequence BETWEEN low AND high
          AND m.message_id IN (SELECT value FROM json_each(?))
    ), receipts AS (
        SELECT parent_message_id,json_extract(body_json,'$.data.tool_call_id') AS call_id,
               json_extract(body_json,'$.data.status') AS status,
               json_extract(body_json,'$.data.additional_kwargs.naiyue_not_executed') AS blocked
        FROM naiyue_chat_messages m JOIN scope USING(session_id,thread_id,reply_id)
        WHERE m.message_type='tool' AND m.checkpoint_ns='' AND m.kind='message'
          AND m.source_thread_id=m.thread_id AND m.sequence BETWEEN low AND high
    )
    SELECT r.call_id,CASE WHEN COUNT(r.name)=COUNT(*) AND MIN(r.name)=MAX(r.name) THEN MIN(r.name) END,
           CASE WHEN COUNT(*)=1 THEN MAX(t.status) END, CASE WHEN COUNT(*)=1 THEN MAX(t.blocked) END
    FROM requests r LEFT JOIN receipts t
        ON t.parent_message_id=r.message_id AND t.call_id=r.call_id
        GROUP BY r.call_id ORDER BY r.call_id""",
        (session, context["thread_id"], context["reply_id"], context["end_sequence"],
         context["response_sequence"], json.dumps(ai_messages)))
    root = {tool["call_id"]: tool for tool in tools if tool["scope"] == "root"}
    for call_id, name, status, blocked in rows:
        if not isinstance(call_id, str) or not call_id:
            continue
        name = name if isinstance(name, str) and name else None
        tool = root.get(call_id)
        if tool is None:
            tool = {"scope": "root", "agent_run_id": None, "call_id": call_id, "name": name, "outcome": None}
            tools.append(tool)
        elif tool["name"] is None and "name" not in tool.get("conflicting_fields", ()):
            tool["name"] = name
        if not name or tool["name"] != name:
            continue
        if tool["outcome"] is None and "outcome" not in tool.get("conflicting_fields", ()):
            if status == "success":
                tool["outcome"] = "success"
            elif status == "error" and blocked:
                tool["outcome"] = "not_executed"
            # An unqualified error receipt cannot distinguish refusal from execution failure.


def _facts(connection, project_id, version, row, start, end):
    session_id, run_id, diag_state, diag_closed, summary_json, usage_closed, usage_partial, active = row
    if active:
        return "active_run", None
    if (diag_state == "open" and not usage_closed) or (not diag_closed and not usage_closed):
        return "unclosed_capture", None
    summary = json.loads(summary_json) if summary_json and diag_closed else {}
    time_fields = {"run_finished": summary.get("finished_at"), "diagnostic_closed": diag_closed,
                   "usage_closed": usage_closed}
    for basis, raw in time_fields.items():
        closed = timestamp(raw)
        if closed:
            break
    else:
        return "missing_time", None
    if not start <= closed < end:
        return None, None

    run_key = (session_id, run_id)
    events = [json.loads(event[0]) for event in connection.execute(
        """SELECT body_json FROM naiyue_diagnostic_events
           WHERE session_id=? AND run_id=? ORDER BY sequence""", run_key)] if diag_closed else []
    tools, ai_messages, valid = _event_keys(events)
    complete = bool(diag_closed and not summary.get("capture_incomplete", True))
    invocations = {invocation["invocation_id"]: dict(invocation) for invocation in connection.execute(
        """SELECT invocation_id,agent_path,purpose,status FROM naiyue_model_invocations
           WHERE session_id=? AND run_id=? ORDER BY invocation_id""", run_key)}
    model_coverage = bool(usage_closed and usage_partial == 0)
    for event in events:
        if event["event_type"] not in ("model_call_started", "model_call_finished"):
            continue
        data, source = event["data"], event["source"]
        identity = data.get("invocation_id")
        if not identity:
            model_coverage = False
            continue
        if identity not in invocations:
            model_coverage = False
            invocations[identity] = {"invocation_id": identity,
                                     "agent_path": source["agent_path"] if source else "root",
                                     "purpose": None, "status": None}
        invocation = invocations[identity]
        if invocation["purpose"] is None:
            invocation["purpose"] = data.get("purpose")
        if invocation["status"] in (None, "started") and data.get("status"):
            invocation["status"] = data["status"]
    invocations = list(invocations.values())
    user_turns, context = _reply_context(connection, version, session_id, ai_messages)
    if context and (not complete or not valid or any(tool["outcome"] is None for tool in tools)):
        _archive_tools(connection, session_id, context, ai_messages, tools)
    raw_status = summary.get("status")
    statuses = {e["data"].get("status") for e in events if e["event_type"] == "run_finished" and e["source"] is None}
    status = raw_status if raw_status in STATUSES and (not statuses or statuses == {raw_status}) else None
    return None, {"key": [project_id, *run_key], "closed_at": closed.isoformat(), "time_basis": basis,
                  "status": status, "mode": summary.get("mode"), "diagnostic_sequence": summary.get("last_sequence"),
                  "diagnostic_capture": diag_state, "diagnostic_reason": summary.get("reason"),
                  "diagnostic_incomplete": not complete, "usage_closed_at": usage_closed,
                  "tools": tools, "tools_complete": complete and valid,
                  "invocations": invocations, "models_complete": bool(model_coverage
                      and all(i["purpose"] is not None for i in invocations if i["agent_path"] == "root")),
                  "model_calls": sum(i["agent_path"] == "root" and i["purpose"] == "agent" for i in invocations),
                  "user_turns": user_turns, "user_context": context,
                  "user_turns_complete": bool(context and complete and summary.get("mode") == "message")}


_KEYS = """WITH diagnostics AS (
    SELECT session_id,run_id FROM naiyue_diagnostic_runs
    WHERE (session_id,run_id) > (?,?) AND (session_id,run_id) <= (?,?)
    ORDER BY session_id,run_id LIMIT ?
), usage AS (
    SELECT session_id,run_id FROM naiyue_model_usage_runs
    WHERE (session_id,run_id) > (?,?) AND (session_id,run_id) <= (?,?)
    ORDER BY session_id,run_id LIMIT ?
), keys AS (SELECT * FROM diagnostics UNION SELECT * FROM usage)"""
_UPPER = """SELECT MAX(session_id) FROM (
    SELECT MAX(session_id) AS session_id FROM naiyue_diagnostic_runs
    UNION ALL SELECT MAX(session_id) FROM naiyue_model_usage_runs
)"""


def _project(project, start, end):
    path = Path(project["database_path"])
    check_restore_marker(path)
    before = path.stat()
    records, skipped = [], {}
    with _reader(path) as connection:
        version = validate_cleanup_database(connection, project["project_id"])
        if version < 10:
            raise ValueError("diagnostic_schema_unavailable")
        last_session = connection.execute(_UPPER).fetchone()[0]
        upper = connection.execute("""SELECT ?,MAX(run_id) FROM (
            SELECT MAX(run_id) AS run_id FROM naiyue_diagnostic_runs WHERE session_id=?
            UNION ALL SELECT MAX(run_id) FROM naiyue_model_usage_runs WHERE session_id=?
        )""", (last_session,) * 3).fetchone() if last_session else None
        after = ("", "")
        while upper:
            # Evidence and its summary share a page transaction. Release before advancing.
            deadline = monotonic() + PAGE_READ_BUDGET_SECONDS
            connection.set_progress_handler(lambda: monotonic() >= deadline, 1000)
            connection.execute("BEGIN")
            try:
                parameters = (*after, *upper, PAGE_SIZE)
                page = connection.execute(_KEYS + """ SELECT k.session_id,k.run_id,d.capture_state,d.closed_at,
                    d.summary_json,u.recording_closed_at,u.capture_incomplete,
                    COALESCE(s.state='running' AND s.run_id=k.run_id,0)
                    FROM keys k
                    LEFT JOIN naiyue_diagnostic_runs d USING(session_id,run_id)
                    LEFT JOIN naiyue_model_usage_runs u USING(session_id,run_id)
                    LEFT JOIN naiyue_sessions s ON s.session_id=k.session_id
                    ORDER BY k.session_id,k.run_id LIMIT ?""",
                                          (*parameters, *parameters, PAGE_SIZE)).fetchall()
                for row in page:
                    try:
                        reason, facts = _facts(connection, project["project_id"], version, row, start, end)
                    except (ValueError, KeyError, TypeError, AttributeError):
                        reason, facts = "invalid_run_evidence", None
                    if reason:
                        skipped[reason] = skipped.get(reason, 0) + 1
                    if facts:
                        records.append(facts)
            finally:
                connection.set_progress_handler(None, 0)
                connection.rollback()
            if not page:
                break
            after = tuple(page[-1][:2])
        unmapped = connection.execute("""SELECT COUNT(*) FROM naiyue_sessions s
            WHERE EXISTS(SELECT 1 FROM naiyue_chat_messages m WHERE m.session_id=s.session_id)
            AND NOT EXISTS(SELECT 1 FROM naiyue_diagnostic_runs d WHERE d.session_id=s.session_id)
            AND NOT EXISTS(SELECT 1 FROM naiyue_model_usage_runs u WHERE u.session_id=s.session_id)""").fetchone()[0]
    check_restore_marker(path)
    after_scan = path.stat()
    if (before.st_dev, before.st_ino) != (after_scan.st_dev, after_scan.st_ino):
        raise ValueError("project_database_replaced")
    return records, {"upper_key": list(upper) if upper else None, "skipped": skipped,
                     "sessions_with_chat_only": unmapped, "in_window": len(records)}


def _overlaps(path, root):
    return path.is_relative_to(root) or root.is_relative_to(path)


def source_projects(data_dir, output):
    registry = data_dir / "projects.sqlite"
    with _reader(registry) as connection:
        projects = _registry(connection)
    if _overlaps(output, data_dir) or any(
        _overlaps(output, Path(root).resolve())
        for project in projects.values() for root in (project["root"], str(Path(project["database_path"]).parent)) if root
    ):
        raise ValueError("output overlaps a registered workspace or product data directory")

    return registry, projects


def collect(data_dir, output, project_ids, start, end):
    registry, projects = source_projects(data_dir, output)
    rows, coverage = [], {}
    for project_id in sorted(set(project_ids) if project_ids else projects):
        project = projects.get(project_id)
        if not project or project["lifecycle"] != "active":
            coverage[project_id] = {"issue": "project_not_active" if project else "project_not_registered"}
            continue
        started = datetime.now(timezone.utc).isoformat()
        try:
            records, details = _project(project, start, end)
            with _reader(registry) as connection:
                current = registrations(connection, 1, project_id)
            if current != [(project_id, project["database_path"], project["lifecycle"], project["revision"])]:
                raise ValueError("registration_changed")
        except (sqlite3.Error, OSError, ValueError, CleanupIssue, ProjectError) as error:
            info = getattr(error, "code", None) or getattr(error, "sqlite_errorname", None)
            known = str(error) if isinstance(error, ValueError) and str(error) in {
                "registration_changed", "project_database_replaced", "restore_incomplete",
                "diagnostic_schema_unavailable"} else None
            coverage[project_id] = {"issue": info or known or type(error).__name__}
        else:
            rows.extend(records)
            coverage[project_id] = details
        coverage[project_id].update(started_at=started, ended_at=datetime.now(timezone.utc).isoformat(),
                                    registration_revision=project["revision"])
    return rows, coverage
