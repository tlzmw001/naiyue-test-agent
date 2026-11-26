"""Call the model or the real project host in an isolated evaluation attempt."""
from collections import Counter
from contextlib import aclosing
from pathlib import Path
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage, convert_to_messages
from langsmith import tracing_context

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.context_usage import compose_model_prompt
from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.application.sessions.project_instructions import BASE_SYSTEM_PROMPT
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import create_builtin_tools

from .collaboration import completed_report
from .multi_turn import session_questions
from .planning import run_file_batch, simulate_plan_batch
from .tool_specs import component_tools
from .trajectory_workspace import snapshot, collect_workspace


def read_tools(settings, case):
    return [spec for spec in create_builtin_tools(settings.tools) if spec.tool.name in case.tools]


def record_messages(evidence, messages, turn):
    answers = []
    batch = evidence["calls"][-1].get("batch", 0) if evidence["calls"] else 0
    for i, message in enumerate(messages):
        if not isinstance(message, AIMessage):
            continue
        batch += 1
        answers.append(message)
        results = {}
        for item in messages[i+1:]:
            if isinstance(item, AIMessage):
                break
            if isinstance(item, ToolMessage):
                results[item.tool_call_id] = item if item.tool_call_id not in results else None
        evidence["output_tokens"].append((message.usage_metadata or {}).get("output_tokens"))
        id_counts = Counter(call["id"] for call in message.tool_calls)
        for call in message.tool_calls:
            result = results.get(call["id"]) if id_counts[call["id"]] == 1 else None
            if result is not None and result.name not in (None, call["name"]):
                result = None
            evidence["calls"].append({
                "turn": turn, "batch": batch, "id": call["id"], "name": call["name"], "args": call["args"],
                "status": result.status if result is not None else None,
                "result": result.content if result is not None else None,
                "not_executed": result.additional_kwargs.get("naiyue_not_executed") if result else None,
            })
        for call in message.invalid_tool_calls:
            evidence["calls"].append({
                "turn": turn, "batch": batch, "id": call.get("id"), "name": call.get("name"),
                "args": {}, "status": "invalid", "result": None,
            })
    evidence["answers"].append(answers[-1].text if answers else "")


async def execute_model(case, model, evidence):
    definitions = component_tools()
    specs = [definitions[name] for name in case.tools]
    if specs:
        model = model.bind_tools([spec.tool for spec in specs])
    roles = [(role.name, role.description) for role in case.roles]
    system = compose_model_prompt(BASE_SYSTEM_PROMPT, [], specs, roles)
    messages = [system, *convert_to_messages(
        [*case.history, ("user", case.turns[0])])]
    if case.mode == "planning":
        await execute_plan(case, model, messages, evidence)
        return
    start = perf_counter()
    try:
        reply = await model.ainvoke(messages)
    finally:
        evidence["turn_seconds"].append(perf_counter() - start)
    record_messages(evidence, [reply], 1)


async def execute_plan(case, model, messages, evidence):
    if case.file_goal:
        with TemporaryDirectory(prefix="naiyue-plan-") as directory:
            root = Path(directory).resolve()
            target = root / case.file_goal.path
            target.write_bytes(case.file_goal.initial.encode())
            await run_plan_rounds(case, model, messages, evidence, root)
            evidence["file_content_matches"] = target.read_bytes() == case.file_goal.expected.encode()
    else:
        await run_plan_rounds(case, model, messages, evidence, None)


async def run_plan_rounds(case, model, messages, evidence, root):
    progress = 0
    max_rounds = 8 if case.file_goal else len(case.plan_steps) + 4
    for round_number in range(1, max_rounds + 1):
        start = perf_counter()
        try:
            reply = await model.ainvoke(messages)
        finally:
            evidence["turn_seconds"].append(perf_counter() - start)
        if not reply.tool_calls or reply.invalid_tool_calls:
            record_messages(evidence, [reply], round_number)
            if reply.invalid_tool_calls:
                evidence["plan_unexpected_calls"] = True
            break
        if root is None:
            progress, results = simulate_plan_batch(case, reply, progress)
        else:
            results = run_file_batch(case, reply, root)
        if any(message.additional_kwargs.get("plan_unexpected") for message in results):
            evidence["plan_unexpected_calls"] = True
        record_messages(evidence, [reply, *results], round_number)
        messages.extend([reply, *results])
    else:
        evidence["plan_round_limit"] = True
    if root is None:
        evidence["plan_steps_completed"] = progress


async def execute_session(case, config, model_id, evidence, user_model_id=None):
    with TemporaryDirectory(prefix="naiyue-eval-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        if case.fixture is None:
            workspace.mkdir()
        else:
            copytree(case.fixture, workspace, ignore=ignore_patterns("__pycache__", "*.pyc"))
        permissions = {}
        if case.trajectory:
            target = case.trajectory.artifact
            allow = [{"tool": name, "path": "${workspace}/" + target.path}
                     for name in case.tools if name in ("write", "edit") and target]
            permissions = {"mode": "NoAsk", "allow": allow}
        settings = AppSettings.model_validate({
            "permissions": permissions,
            "subagents": [
                {"name": role.name, "description": role.description, "model_id": model_id,
                 "system_prompt": "按委派说明读工作区材料，并按说明只回报要求的结果。",
                 "tool_names": ["read"], "max_model_calls": 6}
                for role in case.roles
            ],
            "storage": {"data_dir": str(root / "data")},
            "memory": {"enabled": False, "auto_enabled": False},
        })
        async with open_service_application(
            config, tools=read_tools(settings, case), system_prompt=BASE_SYSTEM_PROMPT,
            response_format=None, settings=settings, initial_active_tools=list(case.tools),
        ) as app:
            project = await app.projects.add_local(workspace, trusted=True, operation_id=uuid4().hex)
            host = await app.projects.open(project.project_id)
            sid = await host.sessions.create(model_id)
            history = [message.model_copy(update={"id": uuid4().hex})
                       for message in convert_to_messages(case.history)]
            before = snapshot(workspace) if case.trajectory else {}
            seen_ids = {message.id for message in history}
            active_run = None
            started = None
            child_runs = []
            if case.collaboration is not None:
                evidence["children"] = []

            def observe(event):
                source = event.source
                if (case.collaboration is not None and event.run_id == active_run
                        and source and event.event_type == "run_finished"):
                    child_runs.append({"agent_id": source.agent_id, "agent_run_id": source.agent_run_id,
                                       "parent_call_id": source.parent_tool_call_id,
                                       "status": event.data.status, "turn": turn})
                if (event.run_id == active_run and event.event_type == "message_delta"
                        and turn == 1 and event.source is None and event.data.text
                        and not evidence["first_text_seconds"]):
                    evidence["first_text_seconds"].append(perf_counter() - started)

            unsubscribe = host.observability.subscribe(observe, session_id=sid)
            try:
                async with aclosing(session_questions(case, config, user_model_id, evidence)) as questions:
                    async for turn, question in questions:
                        messages = [("user", question)]
                        if turn == 1:
                            messages = [*history, *messages]
                        active_run = uuid4().hex
                        started = perf_counter()
                        try:
                            result = await host.runs.start(sid, messages, run_id=active_run)
                        finally:
                            evidence["turn_seconds"].append(perf_counter() - started)
                            active_run = None
                        current = [message for message in result["messages"] if message.id not in seen_ids]
                        seen_ids.update(message.id for message in current)
                        record_messages(evidence, current, turn)
                        if case.simulation:
                            evidence["dialogue"].append({"user": question, "assistant": evidence["answers"][-1]})
                        if result.get("approval_requests") or result.get("run_stopped"):
                            evidence["completed"] = False
                            break
                if case.collaboration is not None:
                    returned_ids = {completed_report(call).get("agent_id") for call in evidence["calls"]
                                    if call["name"] == "subagent"}
                    for child in child_runs:
                        row = {**child, "calls": [], "answers": [], "output_tokens": []}
                        if child["agent_id"] in returned_ids:
                            history = await host.history.subagent(sid, child["agent_id"])
                            record_messages(row, history["messages"], child["turn"])
                        evidence["children"].append(row)
                if case.trajectory:
                    evidence["workspace"] = collect_workspace(case.trajectory, workspace, before)
                    for call in evidence["calls"]:
                        path = call["args"].get("path")
                        if isinstance(path, str) and Path(path).is_absolute():
                            path = Path(path)
                            if path.is_relative_to(workspace.resolve()):
                                call["workspace_path"] = path.relative_to(workspace.resolve()).as_posix()
            finally:
                unsubscribe()


async def execute(case, config, model_id, evidence, user_model_id=None):
    with tracing_context(enabled=False):
        if case.mode == "session":
            await execute_session(case, config, model_id, evidence, user_model_id)
        else:
            catalog = application.load_model_catalog(config)
            try:
                catalog.prepare_async_clients()
                await execute_model(case, catalog.get(model_id), evidence)
            finally:
                await catalog.aclose()
