"""Probe-owned app assembly and finite assertions; never serialize provider bodies."""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
import json

from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import load_model_catalog, COMPACTION_SUMMARY_OUTPUT_METADATA_KEY
from naiyue_test_agent.model_access.messages import content_text
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.builtin import create_builtin_tools
from .provider_acceptance_budget import guard_catalog
from .provider_acceptance_scope import FixturePermission


class AcceptanceCheckFailed(AssertionError):
    def __init__(self, check):
        self.check = check
        super().__init__(check)


def require(condition, check):
    if not condition:
        raise AcceptanceCheckFailed(check)


def paired_tools(messages, names, *, statuses=None):
    calls = [call for message in messages if isinstance(message, AIMessage) for call in message.tool_calls]
    results = [message for message in messages if isinstance(message, ToolMessage)]
    require([call['name'] for call in calls] == names, 'expected_tool_sequence')
    require([message.name for message in results] == names, 'result_tool_sequence')
    require([call['id'] for call in calls] == [message.tool_call_id for message in results], 'paired_results')
    require([message.status for message in results] == (statuses or ['success'] * len(names)), 'tool_status')
    return results


def expected_calls(messages, expected, workspace):
    calls = [call for message in messages if isinstance(message, AIMessage) for call in message.tool_calls]
    require(len(calls) == len(expected), 'fixture_call_count')
    for call, (name, fields) in zip(calls, expected, strict=True):
        require(call['name'] == name, 'fixture_tool_name')
        for key, value in fields.items():
            actual = call['args'].get(key)
            if key == 'path' and isinstance(actual, str):
                actual = (Path(workspace) / actual).resolve()
                value = (Path(workspace) / value).resolve()
            require(actual == value, 'fixture_call_arguments')


def single_line_read(content, expected):
    require(content in (expected, expected + '\n[read: lines 1-1; next_offset=none]'), 'single_line_read_payload')


def final_text(messages):
    require(bool(messages) and isinstance(messages[-1], AIMessage), 'final_assistant')
    require(not messages[-1].tool_calls, 'final_has_no_calls')
    return content_text(messages[-1].content).strip()


@dataclass
class CaseEnvironment:
    config: str
    model_id: str
    root: Path
    budget: object
    events: list = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)

    @asynccontextmanager
    async def open_app(self, names=(), *, settings=None, database='state.sqlite', initial=None, catalog_setup=None):
        settings = settings or AppSettings()
        catalog = load_model_catalog(self.config)
        # Select the agreed model in memory; never create a config/credential copy.
        catalog._models = {self.model_id: catalog.get(self.model_id)}
        catalog.default_model_id = self.model_id
        model = catalog.get(self.model_id)
        model.max_tokens = 1024
        model.metadata = {**(model.metadata or {}), COMPACTION_SUMMARY_OUTPUT_METADATA_KEY: {'tokens': 1024}}
        if catalog_setup is not None:
            catalog_setup(catalog)
        guard_catalog(catalog, self.budget)
        previous = application.load_model_catalog
        application.load_model_catalog = lambda _: catalog
        specs = [replace(spec, permissions=FixturePermission(spec.tool.name, spec.permissions, self.root))
                 for spec in create_builtin_tools(settings.tools, process_settings=settings.process, output_settings=settings.tool_output, progress_settings=settings.progress, retry_settings=settings.tool_retry) if spec.tool.name in names]
        try:
            async with open_async_persistent_application(self.config, database_path=self.root / database,
                    tools=specs, initial_active_tools=initial, settings=settings,
                    system_prompt='Follow the synthetic acceptance task exactly. Use only requested tools. '
                                  'After all requested results, reply with only the requested answer. '
                                  'A refused or failed call must not be retried.', response_format=None) as app:
                app.subscribe_events(self.events.append)
                yield app
        finally:
            application.load_model_catalog = previous
            # The factory normally closes this catalog; a failure before ownership also closes it.
            await catalog.aclose()

    async def session(self, app):
        workspace = self.root / 'work'
        workspace.mkdir(exist_ok=True)
        return await app.acreate_workspace_session(self.model_id, workspace)

    def evidence(self):
        # Usage is vendor reported, deliberately separate from local context estimates.
        usage = []
        for event in self.events:
            if event.event_type == 'model_call_finished':
                data = event.data.model_dump() if hasattr(event.data, 'model_dump') else vars(event.data)
                row = {key: data.get(key) for key in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')}
                if any(value is not None for value in row.values()):
                    usage.append(row)
        delta_ids = {e.data.message_id for e in self.events if e.event_type == 'message_delta'}
        completed_ids = {json.loads(e.data.snapshot_json)['id'] for e in self.events
                         if e.event_type == 'message_completed'}
        summaries = [{key: getattr(e.data, key, None) for key in ('summary_input_tokens', 'summary_output_tokens',
                                                              'summary_cache_read_tokens', 'summary_cache_write_tokens')}
                     for e in self.events if e.event_type == 'compaction_finished']
        tools = [{key: getattr(e.data, key, None) for key in ('tool_name', 'boundary', 'outcome', 'reason')}
                 for e in self.events if e.event_type == 'tool_call_finished']
        return {'stream_confirmed_messages': len(delta_ids & completed_ids),
                'diagnostic': self.diagnostics,
                'stream_unmatched_messages': len(delta_ids - completed_ids),
                'summary_vendor_usage': summaries,
                'tool_outcomes': tools,
                'event_counts': {name: sum(e.event_type == name for e in self.events)
                                 for name in sorted({e.event_type for e in self.events})},
                'vendor_usage': usage}


async def run_turn(app, sid, prompt):
    result = await app.arun(sid, [('user', prompt)])
    require(not result.get('approval_requests'), 'unexpected_approval')
    require(not result.get('run_stopped'), 'unexpected_stop')
    rows = await app.alist_sessions()
    row = next(row for row in rows if row['session_id'] == sid)
    require(row['state'] == 'ready' and row['last_run_status'] == 'completed', 'saved_completed')
    require(await app.aget_messages(sid) == result['messages'], 'saved_messages')
    return result['messages']
