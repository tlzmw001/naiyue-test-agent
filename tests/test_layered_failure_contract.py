"""Layer-owned descriptions and trusted, isolated extension entry points."""

from naiyue_test_agent.application.sessions import session_execution

from test_application_run_events import ToolModel

from native_application_helpers import async_test, build_application

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import ast
import asyncio
from importlib.util import resolve_name
from pathlib import Path
import subprocess
import sys

import pytest
from tool_feedback_helpers import fail_tool_dispatch
from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException, tool

from naiyue_test_agent import application
from naiyue_test_agent.contracts.failures import FailureDescription
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.model_access.errors import describe_model_failure
from naiyue_test_agent.agent_runtime.errors import RuntimeErrorPolicy




class VendorQuotaError(Exception):
    pass


class ToolDataError(Exception):
    pass


class ReportStorageError(Exception):
    pass


def quota(error, source):
    if isinstance(error, VendorQuotaError):
        return FailureDescription('model', 'limit_exceeded', 'vendor_quota', type(error).__name__)
    return None


async def build(monkeypatch, *, failure=None, tools=None, replies=None):
    class Failing(ToolModel):
        def _generate(self, *args, **kwargs):
            raise failure

    model = Failing(responses=[]) if failure else ToolModel(responses=replies or [
        AIMessage(content='ok', response_metadata={'finish_reason': 'stop'}),
    ])
    catalog = ModelCatalog('A', {'A': model})
    monkeypatch.setattr(application, 'load_model_catalog', lambda _path: catalog)
    app = await build_application(
        'synthetic.json', tools=tools or [], system_prompt='', response_format=None,
    )
    events = []
    app.observability.subscribe(events.append)
    return app, events, catalog


@pytest.mark.parametrize('error,expected', [
    (ModelRateLimitError('PRIVATE'), ('limit_exceeded', 'limit_unspecified')),
    (ModelTimeoutError('PRIVATE'), ('transient', 'timeout')),
])
def test_model_framework_errors_have_provider_neutral_description(error, expected):
    data = describe_model_failure(error)
    assert data.source == 'model'
    assert (data.category, data.reason) == expected
    assert data.error_type == type(error).__name__
    assert 'PRIVATE' not in repr(data)


@pytest.mark.parametrize('fields', [
    ('model', 'bad category', 'reason', 'Exception'),
    ('model', 'unknown', 'PRIVATE RAW TEXT', 'Exception'),
    ('forged', 'unknown', 'unclassified', 'Exception'),
    ('model', 'unknown', 'unclassified', 'Invalid\nType'),
])
def test_shared_description_rejects_unsafe_fields(fields):
    with pytest.raises(ValueError):
        FailureDescription(*fields)


def test_model_classifiers_are_model_scoped_and_snapshotted():
    model = ToolModel(responses=[])
    catalog = ModelCatalog('A', {'A': model, 'B': model})
    catalog.configure_error_classifiers('A', [quota])
    before = catalog.failure_describer()
    error = VendorQuotaError('PRIVATE')
    assert catalog.describe_failure('A', error).reason == 'vendor_quota'
    assert catalog.describe_failure('B', error).category == 'unknown'
    catalog.configure_error_classifiers('A', [])
    assert before('A', error).reason == 'vendor_quota'
    assert catalog.describe_failure('A', error).category == 'unknown'


def test_runtime_protected_errors_cannot_be_overridden():
    from naiyue_test_agent.agent_runtime.run_control import UserStop
    from naiyue_test_agent.tools.processes import ProcessCleanupError
    from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected
    from naiyue_test_agent.agent_runtime.errors import ContextCompactionError, ToolBatchSchedulingError

    def forbidden(_error, _source):
        raise AssertionError('Protected classifications must bypass extensions')

    policy = RuntimeErrorPolicy(classifiers=[forbidden])
    for error, reason in [(UserStop(), 'user_stop'),
                          (asyncio.CancelledError(), 'task_cancelled'),
                          (ProcessCleanupError('PRIVATE'), 'tool_outcome_unknown'),
                          (ModelResponseRejected('invalid_tool_calls'), 'invalid_tool_calls'),
                          (ContextCompactionError('PRIVATE'), 'compaction_failed'),
                          (ToolBatchSchedulingError('PRIVATE'), 'tool_batch_scheduling_failed')]:
        assert policy.describe(error, source='runtime').reason == reason


@async_test
async def test_custom_model_error_reaches_model_and_run_events_without_core_edits(monkeypatch):
    error = VendorQuotaError('PRIVATE')
    app, events, _catalog = await build(monkeypatch, failure=error)
    app.observability.configure_errors(model_id='A', model=[quota])
    session = await app.sessions.create('A')
    with pytest.raises(VendorQuotaError) as caught:
        await app.runs.start(session, [('user', 'probe')])
    assert caught.value is error
    model_end = next(e.data for e in events if e.event_type == 'model_call_finished')
    terminal = events[-1].data
    assert terminal.status == 'failed'
    assert model_end.status == 'failed' and model_end.error_type == terminal.failure.error_type
    assert terminal.failure.reason == 'vendor_quota'


@async_test
async def test_custom_fatal_tool_description_is_reported_to_application(monkeypatch):
    error = ToolDataError('PRIVATE')

    @tool
    def probe() -> str:
        """Raise a synthetic fatal tool error without side effects."""
        return 'unreachable after the dispatch fault'

    def describe(error, source):
        if isinstance(error, ToolDataError):
            return FailureDescription('tool', 'tool_error', 'data_unavailable', 'ToolDataError')
        return None

    fail_tool_dispatch(monkeypatch, 'probe', error)
    app, events, _catalog = await build(monkeypatch, tools=[ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))], replies=[
        AIMessage(content='', tool_calls=[{'name': 'probe', 'args': {}, 'id': 'c1'}],
                  response_metadata={'finish_reason': 'tool_calls'}),
    ])
    app.observability.configure_errors(runtime=[describe])
    with pytest.raises(ToolDataError) as caught:
        await app.runs.start(await app.sessions.create('A'), [('user', 'probe')])
    assert caught.value is error
    tool_end = next(e.data for e in events if e.event_type == 'tool_call_finished')
    assert tool_end.failure.reason == 'data_unavailable'
    assert events[-1].data.failure == tool_end.failure


@async_test
async def test_custom_recoverable_tool_uses_existing_framework_feedback(monkeypatch):
    class Recoverable(ToolException):
        pass

    @tool
    def probe() -> str:
        """Raise a recoverable error without side effects."""
        raise Recoverable('Input is unavailable; choose another input.')

    app, events, _catalog = await build(monkeypatch, tools=[ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))], replies=[
        AIMessage(content='', tool_calls=[{'name': 'probe', 'args': {}, 'id': 'c1'}],
                  response_metadata={'finish_reason': 'tool_calls'}),
        AIMessage(content='recovered', response_metadata={'finish_reason': 'stop'}),
    ])
    result = await app.runs.start(await app.sessions.create('A'), [('user', 'probe')])
    feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert feedback.status == 'error' and feedback.tool_call_id == 'c1'
    assert feedback.content == 'Input is unavailable; choose another input.'
    assert result['messages'][-1].content == 'recovered'
    assert events[-1].data.status == 'completed'


@async_test
async def test_application_extension_is_local_and_has_correct_stage(monkeypatch):
    app, events, _catalog = await build(monkeypatch)
    other, other_events, _other_catalog = await build(monkeypatch)
    error = ReportStorageError('PRIVATE')

    def describe(error, source):
        if isinstance(error, ReportStorageError):
            return FailureDescription('application', 'storage_error', 'report_unavailable', 'ReportStorageError')
        return None

    app.observability.configure_errors(application=[describe])

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(session_execution, 'prepare_run_resources', fail)
    for item in (app, other):
        with pytest.raises(ReportStorageError) as caught:
            await item.runs.start(await item.sessions.create('A'), [('user', 'probe')])
        assert caught.value is error
    assert events[-1].data.failure.reason == 'report_unavailable'
    assert events[-1].data.failure.stage == 'prepare'
    assert other_events[-1].data.failure.category == 'unknown'


@async_test
async def test_invalid_extension_does_not_expose_raw_text_or_replace_error(monkeypatch, caplog):
    error = VendorQuotaError('PRIVATE_ORIGINAL')
    app, events, _catalog = await build(monkeypatch, failure=error)

    def invalid(_error, _source):
        raise ValueError('PRIVATE_DIAGNOSTIC')

    app.observability.configure_errors(model_id='A', model=[invalid])
    with pytest.raises(VendorQuotaError) as caught:
        await app.runs.start(await app.sessions.create('A'), [('user', 'probe')])
    assert caught.value is error
    assert events[-1].data.failure.category == 'unknown'
    assert 'PRIVATE_' not in caplog.text + repr(events)


def test_layer_imports_are_independent():
    code = '''import sys
import naiyue_test_agent.contracts.failures
assert not any(n.startswith('langchain') for n in sys.modules)
import naiyue_test_agent.model_access
assert not any(n.startswith(('naiyue_test_agent.agent_runtime', 'naiyue_test_agent.application')) for n in sys.modules)
import naiyue_test_agent.tools
assert not any(n.startswith(('naiyue_test_agent.agent_runtime', 'naiyue_test_agent.application')) for n in sys.modules)
import naiyue_test_agent.agent_runtime
assert not any(n.startswith('naiyue_test_agent.application') for n in sys.modules)
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for layer, forbidden in [('contracts', ('model_access', 'agent_runtime', 'application', 'web')),
                             ('model_access', ('agent_runtime', 'application', 'web')),
                             ('tools', ('agent_runtime', 'application', 'web')),
                             ('agent_runtime', ('application', 'web'))]:
        for path in (Path('naiyue_test_agent') / layer).rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    package = '.'.join(path.parent.parts)
                    module = resolve_name('.' * node.level + (node.module or ''), package)
                    names = [module, *(module + '.' + item.name for item in node.names)]
                elif isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                else:
                    continue
                assert not any(name.startswith('naiyue_test_agent.' + blocked)
                               for name in names for blocked in forbidden), (path, node.lineno)
