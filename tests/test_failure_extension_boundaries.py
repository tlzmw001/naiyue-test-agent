"""Matched unknown descriptions and legacy entry points must stay meaningful."""

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import asyncio
from threading import Event, Thread

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from naiyue_test_agent import application
from naiyue_test_agent.contracts.failures import FailureDescription
from naiyue_test_agent.agent_runtime.errors import RuntimeErrorPolicy, run_failure_data
from naiyue_test_agent.agent_runtime.run_services import RunServices, bind_run_services
from naiyue_test_agent.model_access import ModelCatalog, ModelConfigError
from test_layered_failure_contract import ToolModel, ReportStorageError, VendorQuotaError, quota, build


def test_application_unknown_match_is_not_treated_as_no_match(monkeypatch):
    app, events, _catalog = build(monkeypatch)
    error = ReportStorageError('PRIVATE')

    def describe(_error, _source):
        return FailureDescription('application', 'unknown', 'report_unclassified', 'ReportStorageError')

    def fail(*_args, **_kwargs):
        raise error

    app.configure_error_classifiers(application=[describe])
    monkeypatch.setattr(application.session_service, 'invoke_agent_run', fail)
    with pytest.raises(ReportStorageError) as caught:
        app.run(app.create_session('A'), [('user', 'probe')])
    assert caught.value is error
    assert events[-1].data.failure.reason == 'report_unclassified'


def test_explicit_default_failure_conversion_is_independent_of_bound_model_catalog():
    catalog = ModelCatalog('A', {'A': ToolModel(responses=[])})
    error = TimeoutError('PRIVATE')
    policy = RuntimeErrorPolicy()
    before = run_failure_data(policy.describe(error, source='model'))
    with bind_run_services(RunServices(errors=RuntimeErrorPolicy(model_describer=catalog.failure_describer()))):
        during = run_failure_data(policy.describe(error, source='model'))
    assert (before.category, before.reason) == ('transient', 'timeout')
    assert during == before


def test_model_mapping_is_local_to_each_application(monkeypatch):
    error = VendorQuotaError('PRIVATE')
    first, events, _catalog = build(monkeypatch, failure=error)
    second, second_events, _other = build(monkeypatch, failure=error)
    first.configure_error_classifiers(model_id='A', model=[quota])
    for app in (first, second):
        with pytest.raises(VendorQuotaError) as caught:
            app.run(app.create_session('A'), [('user', 'probe')])
        assert caught.value is error
    assert events[-1].data.failure.reason == 'vendor_quota'
    assert second_events[-1].data.failure.reason == 'unclassified'


def test_async_custom_model_is_described_once_and_keeps_original(monkeypatch):
    from naiyue_test_agent.application.async_application import AsyncApplication

    async def scenario():
        error = VendorQuotaError('PRIVATE')
        sync, events, catalog = build(monkeypatch, failure=error)
        app = AsyncApplication(catalog, sync._sessions, sync._agent, sync._tool_registry)
        calls = []

        def describe(error, source):
            calls.append(error)
            return quota(error, source)

        try:
            app.configure_error_classifiers(model_id='A', model=[describe])
            session = await app.acreate_session('A')
            with pytest.raises(VendorQuotaError) as caught:
                await app.arun(session, [('user', 'probe')])
            assert caught.value is error
            assert calls == [error]
            model_end = next(e.data for e in events if e.event_type == 'model_call_finished')
            assert events[-1].data.failure == model_end.failure
            assert events[-1].data.failure.reason == 'vendor_quota'
        finally:
            await app.aclose()

    asyncio.run(scenario())


def test_configuration_is_atomic_and_rejects_async_classifiers(monkeypatch):
    app, _events, catalog = build(monkeypatch)

    async def classify(_error, _source):
        return None

    with pytest.raises(ValueError, match='synchronous'):
        app.configure_error_classifiers(model_id='A', model=[quota], runtime=[classify])
    assert catalog.describe_failure('A', VendorQuotaError()).reason == 'unclassified'

    with pytest.raises(ModelConfigError, match='Unknown model_id'):
        app.configure_error_classifiers(model_id='missing', model=[quota], application=[quota])
    assert app._sessions._application_classifiers == ()


def test_configuration_rejected_while_real_run_is_busy(monkeypatch):
    entered, release = Event(), Event()

    @tool
    def probe() -> str:
        """Wait for a test signal without side effects."""
        entered.set()
        assert release.wait(5)
        return 'ok'

    app, _events, _catalog = build(monkeypatch, tools=[ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))], replies=[
        AIMessage(content='', tool_calls=[{'name': 'probe', 'args': {}, 'id': 'c1'}],
                  response_metadata={'finish_reason': 'tool_calls'}),
        AIMessage(content='done', response_metadata={'finish_reason': 'stop'}),
    ])
    session = app.create_session('A')
    outcomes = []

    def run():
        try:
            outcomes.append(app.run(session, [('user', 'probe')]))
        except BaseException as error:
            outcomes.append(error)

    thread = Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(application.SessionBusyError):
            app.configure_error_classifiers(runtime=[])
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and len(outcomes) == 1
    assert isinstance(outcomes[0], dict), outcomes
    app.configure_error_classifiers(runtime=[])


@pytest.mark.parametrize('invalid', ['source', 'error_type'])
def test_model_extension_invalid_contract_falls_back(monkeypatch, invalid, caplog):
    error = VendorQuotaError('PRIVATE')
    app, events, _catalog = build(monkeypatch, failure=error)

    def describe(_error, _source):
        return FailureDescription('application' if invalid == 'source' else 'model',
            'limit_exceeded', 'vendor_quota', 'OtherError' if invalid == 'error_type' else 'VendorQuotaError')

    app.configure_error_classifiers(model_id='A', model=[describe])
    with pytest.raises(VendorQuotaError) as caught:
        app.run(app.create_session('A'), [('user', 'probe')])
    assert caught.value is error
    assert events[-1].data.failure.reason == 'unclassified'
    assert events[-1].data.failure.error_type == 'VendorQuotaError'
    assert 'PRIVATE' not in caplog.text + repr(events)


@pytest.mark.parametrize('control', ['graph', 'process'])
def test_control_errors_do_not_enter_extension_classifiers(control):
    from langgraph.errors import GraphBubbleUp

    calls = []

    def classify(error, source):
        calls.append(error)
        return None

    error = GraphBubbleUp() if control == 'graph' else KeyboardInterrupt()
    policy = RuntimeErrorPolicy(classifiers=[classify])
    description = policy.describe(error)
    assert description.source == 'runtime'
    assert description.category == 'unknown'
    assert calls == []


def test_contextual_classifier_receives_actual_boundary():
    calls = []

    def describe(error, source):
        calls.append(source)
        return FailureDescription(source, 'custom_error', 'boundary_failure', type(error).__name__)

    policy = RuntimeErrorPolicy(classifiers=[describe])
    error = RuntimeError('PRIVATE')
    for source in ('runtime', 'tool'):
        description = policy.describe(error, source=source)
        assert description.source == source
        assert description.reason == 'boundary_failure'
    assert calls == ['runtime', 'tool']


def test_contextual_model_and_application_classifiers(monkeypatch):
    from naiyue_test_agent.application.failure_policy import ApplicationErrorPolicy
    calls = []

    def describe(error, source):
        calls.append(source)
        return FailureDescription(source, 'custom_error', 'boundary_failure', type(error).__name__)

    catalog = ModelCatalog('A', {'A': ToolModel(responses=[])})
    catalog.configure_error_classifiers('A', [describe])
    model = catalog.describe_failure('A', VendorQuotaError())
    app = ApplicationErrorPolicy([describe]).describe(ReportStorageError())
    assert (model.source, model.reason) == ('model', 'boundary_failure')
    assert (app.source, app.reason) == ('application', 'boundary_failure')
    assert calls == ['model', 'application']


def test_explicit_source_classifier_keeps_extra_captured_default():
    from naiyue_test_agent.application.failure_policy import ApplicationErrorPolicy

    def describe(error, source, reason='captured_reason'):
        return FailureDescription('application', 'custom_error', reason, type(error).__name__)

    assert ApplicationErrorPolicy([describe]).describe(ReportStorageError()).reason == 'captured_reason'
