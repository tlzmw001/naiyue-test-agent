"""Three real WebService setting entries share execution, not admission or results."""

import asyncio
import inspect
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from naiyue_test_agent.application.errors import SessionBusyError, SessionManualReviewRequiredError, SessionPendingApprovalError
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.model_access import ModelConfigError
from naiyue_test_agent.web.errors import WebError
from naiyue_test_agent.web.service import WebService
from test_async_application import StreamingToolModel
from test_web_api import web_client


PRIVATE = 'PRIVATE_SETTING_INPUT'
CASES = [
    ('select_thinking', 'select_thinking', ('model-A', 'high'), 'thinking_unavailable',
     'web_thinking_selection_invalid', 'web_thinking_selection_failed',
     {'model_id': 'model-A', 'thinking_level': 'high'}),
    ('select_tools', 'select_tools', (['read'],), 'invalid_tool_selection',
     'web_tool_selection_invalid', 'web_tool_selection_failed', {'active_tools': ['grep']}),
    ('select_permission_mode', 'select_permission_mode', ('NoAsk',), 'permission_mode_unavailable',
     'web_permission_mode_unavailable', 'web_permission_mode_failed', {'permission_mode': 'Default'}),
]


@pytest.fixture(params=CASES, ids=['thinking', 'tools', 'permission-mode'])
def setting(request):
    method, setter, args, invalid_code, invalid_event, failure_event, expected = request.param
    application = SimpleNamespace(sessions=SimpleNamespace(
        status=AsyncMock(return_value={'workspace_root': None}),
        active_tools=Mock(return_value=('grep',)), permission_mode=Mock(return_value='Default')),
        runs=SimpleNamespace(pending_approvals=Mock(return_value=[])))
    operation = AsyncMock()
    setattr(application.sessions, setter, operation)
    service = WebService(application, SimpleNamespace(latest={}), SimpleNamespace(workspace_root=None, workspace_id=None))
    return SimpleNamespace(service=service, application=application, method=method, setter=setter,
        args=args, operation=operation, invalid_code=invalid_code, invalid_event=invalid_event,
        failure_event=failure_event, expected=expected)


def test_actual_entries_await_shared_boundary_once_and_keep_confirmed_results(setting, monkeypatch, caplog):
    async def scenario():
        original = setting.service._apply_setting
        calls = []

        async def observe(operation, **policy):
            assert inspect.iscoroutine(operation)
            assert setting.operation.await_count == 0
            calls.append(policy)
            return await original(operation, **policy)

        monkeypatch.setattr(setting.service, '_apply_setting', observe)
        result = await getattr(setting.service, setting.method)('session-A', *setting.args)
        assert result == setting.expected
        setting.operation.assert_awaited_once_with('session-A', *setting.args)
        setting.application.sessions.status.assert_awaited_once_with('session-A')
        assert calls == [{'invalid_code': setting.invalid_code, 'invalid_event': setting.invalid_event,
                          'failure_event': setting.failure_event}]
        if setting.method == 'select_tools':
            setting.application.sessions.active_tools.assert_called_once_with('session-A')
        elif setting.method == 'select_permission_mode':
            setting.application.sessions.permission_mode.assert_called_once_with('session-A')

    with caplog.at_level(logging.DEBUG, logger='naiyue_test_agent.web.service'):
        asyncio.run(scenario())
    assert not caplog.records


@pytest.mark.parametrize('error_type', [ValueError, ModelConfigError])
def test_invalid_selection_preserves_specific_422_and_safe_warning(setting, error_type, caplog):
    setting.operation.side_effect = error_type(PRIVATE)
    with pytest.raises(WebError) as caught:
        asyncio.run(getattr(setting.service, setting.method)('session-A', *setting.args))
    assert (caught.value.code, caught.value.status) == (setting.invalid_code, 422)
    setting.operation.assert_awaited_once_with('session-A', *setting.args)
    assert [(row.levelname, row.getMessage(), row.exc_info) for row in caplog.records] == [
        ('WARNING', setting.invalid_event, None)]
    assert PRIVATE not in caplog.text and PRIVATE not in str(caught.value)


@pytest.mark.parametrize(('error_type', 'detail', 'code', 'status'), [
    (RuntimeError, PRIVATE, 'operation_failed', 500),
    (KeyError, PRIVATE, 'session_not_found', 404),
    (SessionPersistenceError, 'session_save_unconfirmed', 'session_save_unconfirmed', 409),
    (SessionPersistenceError, 'session_not_found', 'session_not_found', 404),
    (SessionPersistenceError, PRIVATE, 'session_unavailable', 409),
    (SessionBusyError, PRIVATE, 'session_busy', 409),
    (SessionManualReviewRequiredError, PRIVATE, 'manual_review_required', 409),
    (SessionPendingApprovalError, PRIVATE, 'approval_required', 409),
])
def test_other_errors_keep_public_mapping_and_type_only_diagnostic(setting, error_type, detail, code, status, caplog):
    setting.operation.side_effect = error_type(detail)
    with pytest.raises(WebError) as caught:
        asyncio.run(getattr(setting.service, setting.method)('session-A', *setting.args))
    assert (caught.value.code, caught.value.status) == (code, status)
    setting.operation.assert_awaited_once_with('session-A', *setting.args)
    assert [(row.levelname, row.getMessage(), row.exc_info) for row in caplog.records] == [
        ('ERROR', f'{setting.failure_event} error_type={error_type.__name__}', None)]
    assert PRIVATE not in caplog.text and PRIVATE not in str(caught.value)


def test_existing_web_error_preserves_identity(setting, caplog):
    failure = WebError('session_recovery_blocked')
    setting.operation.side_effect = failure
    with pytest.raises(WebError) as caught:
        asyncio.run(getattr(setting.service, setting.method)('session-A', *setting.args))
    assert caught.value is failure
    assert [row.getMessage() for row in caplog.records] == [f'{setting.failure_event} error_type=WebError']


@pytest.mark.parametrize('error_type', [asyncio.CancelledError, GeneratorExit])
def test_cancellation_and_base_exception_propagate_original_without_logging(setting, error_type, caplog):
    failure = error_type(PRIVATE)
    setting.operation.side_effect = failure
    with pytest.raises(error_type) as caught:
        asyncio.run(getattr(setting.service, setting.method)('session-A', *setting.args))
    assert caught.value is failure
    setting.operation.assert_awaited_once_with('session-A', *setting.args)
    assert not caplog.records


def test_stop_during_await_reaches_setter_cleanup_without_success_getter(setting, caplog):
    async def scenario():
        started, cleaned = asyncio.Event(), asyncio.Event()
        observed = []

        async def execute(*args):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError as error:
                observed.append(error)
                raise
            finally:
                cleaned.set()

        setting.operation.side_effect = execute
        task = asyncio.create_task(getattr(setting.service, setting.method)('session-A', *setting.args))
        await started.wait()
        task.cancel(PRIVATE)
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert cleaned.is_set() and caught.value is observed[0]
        setting.operation.assert_awaited_once_with('session-A', *setting.args)
        setting.application.sessions.active_tools.assert_not_called()
        setting.application.sessions.permission_mode.assert_not_called()

    asyncio.run(scenario())
    assert not caplog.records


@pytest.mark.parametrize(('gate', 'code', 'status'), [
    ('status-error', 'session_not_found', 404),
    ('workspace', 'session_not_found', 404),
    ('recovery', 'session_recovery_blocked', 409),
])
def test_admission_finishes_before_operation_is_created(setting, gate, code, status, caplog):
    factory = Mock(side_effect=AssertionError('must not create the setter coroutine'))
    setattr(setting.application.sessions, setting.setter, factory)
    if gate == 'status-error':
        setting.application.sessions.status.side_effect = KeyError(PRIVATE)
    elif gate == 'workspace':
        setting.application.sessions.status.return_value = {'workspace_root': '/other-workspace'}
    else:
        setting.application.sessions.status.return_value = {'workspace_root': None, 'recovery': {}}
    with pytest.raises(WebError) as caught:
        asyncio.run(getattr(setting.service, setting.method)('session-A', *setting.args))
    assert (caught.value.code, caught.value.status) == (code, status)
    factory.assert_not_called()
    setting.operation.assert_not_awaited()
    assert not caplog.records


@pytest.mark.parametrize('setting', CASES[1:], indirect=True, ids=['tools', 'permission-mode'])
def test_success_getter_remains_outside_operation_error_mapping(setting, caplog):
    getter = setting.application.sessions.active_tools if setting.method == 'select_tools' else setting.application.sessions.permission_mode
    failure = ValueError(PRIVATE)
    getter.side_effect = failure
    with pytest.raises(ValueError) as caught:
        asyncio.run(getattr(setting.service, setting.method)('session-A', *setting.args))
    assert caught.value is failure
    setting.operation.assert_awaited_once_with('session-A', *setting.args)
    assert not caplog.records


@pytest.mark.parametrize(('endpoint', 'setter', 'body', 'invalid_code'), [
    ('thinking', 'select_thinking', {'model_id': 'A', 'thinking_level': 'high'}, 'thinking_unavailable'),
    ('tools', 'select_tools', {'active_tools': ['read']}, 'invalid_tool_selection'),
    ('permission-mode', 'select_permission_mode', {'permission_mode': 'NoAsk'}, 'permission_mode_unavailable'),
])
@pytest.mark.parametrize('invalid', [True, False], ids=['invalid', 'unexpected'])
def test_http_settings_use_same_safe_error_contract(monkeypatch, tmp_path, caplog, endpoint, setter, body, invalid_code, invalid):
    async def scenario():
        model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, model) as (client, app):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            operation = AsyncMock(side_effect=ValueError(PRIVATE) if invalid else RuntimeError(PRIVATE))
            monkeypatch.setattr(app.state.service.application.sessions, setter, operation)
            response = await client.patch(f'/api/v1/scopes/chats/sessions/{sid}/{endpoint}', json=body)
            assert response.status_code == (422 if invalid else 500)
            assert response.json() == {'error': {'code': invalid_code if invalid else 'operation_failed'}}
            assert PRIVATE not in response.text
            operation.assert_awaited_once()
            assert model.seen == []
    asyncio.run(scenario())
    records = [row for row in caplog.records if row.name == 'naiyue_test_agent.web.service']
    assert len(records) == 1 and records[0].exc_info is None
    assert PRIVATE not in caplog.text
