"""Explicit application ownership without duplicate session routes or hidden setup."""

import asyncio

import pytest

from naiyue_test_agent.application import run_events
from naiyue_test_agent.application.failure_policy import ApplicationErrorPolicy
from naiyue_test_agent.application.session_service import SessionService
from naiyue_test_agent.agent_runtime.errors import RuntimeErrorPolicy
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel, make_app
from test_async_persistent import factory


def service():
    return SessionService(ModelCatalog('A', {'A': StreamingToolModel(replies=[])}))


def test_loaded_owner_lookup_keeps_registry_and_runtime_boundaries():
    sessions = service()
    sid = sessions.create_session('A')
    owner = sessions.require(sid)
    assert sessions.require(sid) is owner
    assert owner.describe_session()['session_id'] == sid
    with pytest.raises(KeyError):
        sessions.require('missing')
    assert sessions.loaded_session_ids() == (sid,)
    from naiyue_test_agent.agent_runtime.session_port import SessionPort
    assert not hasattr(SessionPort, 'require')
    for obsolete in ('describe_session', 'get_thread_id', 'snapshot_for_context',
                     'approval_seed_identity', 'select_model', 'select_thinking',
                     'get_active_tools', 'set_active_tools', 'get_permission_mode',
                     'set_permission_mode', 'steer', 'follow_up', 'request_stop',
                     'pending_tool_approvals', 'pending_approval_batches',
                     'pending_subagent_approvals', 'submit_subagent_approval',
                     'prepare_root_approval_submission', 'validate_root_approval_stop',
                     'subagent_status'):
        assert not hasattr(sessions, obsolete), obsolete


@pytest.mark.parametrize('method', ['steer', 'follow_up'])
@pytest.mark.parametrize('value', ['', '  ', None, 1])
def test_control_text_validation_precedes_unknown_session_lookup(monkeypatch, method, value):
    async def scenario():
        app = await make_app(monkeypatch, StreamingToolModel(replies=[]))
        try:
            with pytest.raises(ValueError, match='non-empty'):
                getattr(app, method)('missing', value)
            with pytest.raises(KeyError):
                getattr(app, method)('missing', 'valid')
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_observation_accepts_complete_policies_without_constructing_defaults(monkeypatch):
    runtime, application = RuntimeErrorPolicy(), ApplicationErrorPolicy()
    def unexpected():
        raise AssertionError('Supplied policies must not create discarded defaults')
    monkeypatch.setattr(run_events, 'RuntimeErrorPolicy', unexpected)
    monkeypatch.setattr(run_events, 'ApplicationErrorPolicy', unexpected)
    observation = run_events.RunObservation(
        run_events.ApplicationEventSubscriptions().publisher('s', 'r'), 'message',
        runtime_errors=runtime, application_errors=application)
    assert observation.runtime_errors is runtime
    assert observation.application_errors is application
    with observation.bind():
        from naiyue_test_agent.agent_runtime.run_services import current_run_services
        assert current_run_services().errors is runtime


def test_root_admission_supplies_policies_at_construction(monkeypatch):
    from naiyue_test_agent.application import session_service
    seen = []
    original = run_events.RunObservation
    def construct(*args, **kwargs):
        seen.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(session_service, 'RunObservation', construct)
    sessions = service()
    sid = sessions.create_session('A')
    owner, run = sessions._admit(sid, 'r', 'message')
    assert seen[0]['runtime_errors'] is run.observation.runtime_errors
    assert seen[0]['application_errors'] is run.observation.application_errors
    assert owner.is_busy()


@pytest.mark.parametrize('invalid', [0, -1, True, False, 1.5, '2', None])
@pytest.mark.parametrize('method', ['arun', 'aresume_tool_approval'])
def test_invalid_concurrency_is_rejected_before_admission(invalid, method):
    async def scenario():
        sessions = service()
        sid = sessions.create_session('A')
        events = []
        sessions.subscribe_events(events.append)
        with pytest.raises(ValueError, match='Tool concurrency must be a positive integer'):
            await getattr(sessions, method)(None, sid, [], max_concurrency=invalid)
        assert not sessions.require(sid).is_busy()
        assert events == []
    asyncio.run(scenario())


def test_shared_tool_concurrency_rule_rejects_bool_and_keeps_valid_values():
    from naiyue_test_agent.agent_runtime.tool_execution.async_batch import validate_tool_concurrency
    for value in (1, 4):
        validate_tool_concurrency(value)
    for value in (True, False, None, '1', 0, -1, 1.5):
        with pytest.raises(ValueError, match='Tool concurrency must be a positive integer'):
            validate_tool_concurrency(value)


def test_session_execution_is_an_explicit_internal_collaboration():
    from naiyue_test_agent.application.agent_session import AgentSession
    assert callable(AgentSession.arun)
    assert not hasattr(AgentSession, '_arun')


@pytest.mark.parametrize('fail_assembly', [False, True])
def test_compact_requires_successful_assembly_before_admission(monkeypatch, fail_assembly):
    async def scenario():
        sessions = service()
        sid = sessions.create_session('A')
        assert sessions._system_prompt is None
        if fail_assembly:
            import naiyue_test_agent.agent_runtime as runtime
            failure = ValueError('assembly failed')
            def fail(*args, **kwargs):
                raise failure
            monkeypatch.setattr(runtime, '_assemble_interactive_runtime', fail)
            with pytest.raises(ValueError) as caught:
                sessions.assemble_runtime(None, system_prompt=None, response_format=None)
            assert caught.value is failure
        events = []
        sessions.subscribe_events(events.append)
        with pytest.raises(RuntimeError, match='assembled'):
            await sessions.acompact(None, sid)
        assert not sessions.require(sid).is_busy()
        assert events == []
    asyncio.run(scenario())


def test_application_owns_usage_close_without_reading_persistence_internals(monkeypatch, tmp_path):
    async def scenario():
        async with factory(monkeypatch, tmp_path / 'sessions.sqlite', StreamingToolModel(replies=[])) as app:
            usage = app._usage
            calls = []
            original = usage._close
            async def close():
                calls.append('usage')
                await original()
            monkeypatch.setattr(usage, '_close', close)
            app._persistence._usage = None
            await app.aclose()
            await app.aclose()
            assert calls == ['usage']
            assert app._sessions._events._handlers == {}
        assert calls == ['usage']
    asyncio.run(scenario())


def test_application_settings_are_aggregated_outside_the_tool_package(tmp_path):
    from naiyue_test_agent.settings import AppSettings, load_settings
    from naiyue_test_agent.tools import settings as tool_settings
    from naiyue_test_agent.tools import create_builtin_tools
    assert not hasattr(tool_settings, 'ToolSettings')
    assert not hasattr(tool_settings, 'load_tool_settings')
    assert not hasattr(tool_settings, 'DiagnosticSettings')
    settings = load_settings(tmp_path / 'missing.toml')
    assert settings == AppSettings()
    assert settings.max_active_runs == 10
    tools = create_builtin_tools(settings.tools, process_settings=settings.process,
        shell_task_settings=settings.shell_tasks, progress_settings=settings.progress)
    assert {spec.tool.name for spec in tools} == {
        'read', 'ls', 'find', 'grep', 'write', 'edit', 'bash', 'powershell', 'subagent', 'subagent_list', 'aitest_kit', 'web_search', 'web_fetch', 'task_list', 'task_output', 'task_stop'}


def test_catalog_client_preparation_has_an_explicit_consumer_method():
    catalog = ModelCatalog('A', {'A': StreamingToolModel(replies=[])})
    catalog.prepare_async_clients()
    assert not hasattr(catalog, '_prepare_async_clients')
