"""Diagnostic failures must never replace a model boundary's original error."""

from runtime_context_helpers import RunIdentity, typed_context
from native_application_helpers import async_test

from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from naiyue_test_agent.agent_runtime import call_observation
from naiyue_test_agent.model_access import errors as model_errors


@async_test
@pytest.mark.parametrize('diagnostic', ['classification', 'unknown_details'])
async def test_model_diagnostic_failure_preserves_original(monkeypatch, diagnostic):
    model = FakeMessagesListChatModel(responses=[])
    middleware = call_observation.make_model_selection_middleware(
        lambda _session, agent_path='root': ('A', model),
    )
    request = SimpleNamespace(runtime=SimpleNamespace(context=typed_context(identity=RunIdentity(session_id='synthetic-session', run_id='synthetic-run'))))
    request.override = lambda **_kwargs: request
    await middleware.abefore_model({}, request.runtime)
    primary = RuntimeError('PRIVATE_PRIMARY')

    called = []

    def broken(_error):
        called.append(True)
        raise ValueError('PRIVATE_DIAGNOSTIC')

    name = 'classify_model_failure' if diagnostic == 'classification' else 'unknown_failure_diagnostics'
    owner = model_errors if diagnostic == 'classification' else call_observation
    monkeypatch.setattr(owner, name, broken)

    async def handler(_request):
        raise primary

    with pytest.raises(RuntimeError) as caught:
        await middleware.awrap_model_call(request, handler)
    assert caught.value is primary
    assert called == [True]


@async_test
async def test_summary_diagnostic_failure_keeps_original_cause(monkeypatch):
    from naiyue_test_agent.agent_runtime import context_compaction
    from test_context_compaction_fallback import _case

    primary = TimeoutError('PRIVATE_SUMMARY')
    calls = []

    def broken(_error):
        calls.append(True)
        raise ValueError('PRIVATE_DIAGNOSTIC')

    app, session, _model, messages, *_rest = await _case(monkeypatch, primary, send_margin=-1)
    monkeypatch.setattr(model_errors, 'classify_model_failure', broken)
    if hasattr(context_compaction, 'classify_model_failure'):
        monkeypatch.setattr(context_compaction, 'classify_model_failure', broken)
    with pytest.raises(context_compaction.ContextCompactionError) as caught:
        await app.runs.start(session, messages)
    assert caught.value.__cause__ is primary
    assert calls == [True]


@async_test
async def test_runtime_compaction_error_is_not_reclassified_as_model_unknown(monkeypatch):
    from naiyue_test_agent.agent_runtime.context_compaction import ContextCompactionError
    from test_context_compaction_fallback import _case

    app, session, _model, messages, *_rest = await _case(monkeypatch, RuntimeError('PRIVATE'), send_margin=-1)
    events = []
    app.observability.subscribe(events.append)
    with pytest.raises(ContextCompactionError):
        await app.runs.start(session, messages)
    assert events[-1].data.failure.category == 'context_error'
    assert events[-1].data.failure.reason == 'compaction_failed'


@async_test
async def test_summary_uses_configured_model_failure_mapping(monkeypatch):
    from naiyue_test_agent.agent_runtime.context_compaction import ContextCompactionError
    from test_context_compaction_fallback import _case
    from test_layered_failure_contract import VendorQuotaError, quota

    primary = VendorQuotaError('PRIVATE')
    app, session, _model, messages, *_rest = await _case(monkeypatch, primary, send_margin=-1)
    calls = []

    def describe(error, source):
        if isinstance(error, VendorQuotaError):
            calls.append(error)
        return quota(error, source)

    app.observability.configure_errors(model_id='A', model=[describe])
    with pytest.raises(ContextCompactionError) as caught:
        await app.runs.start(session, messages)
    assert caught.value.__cause__ is primary
    assert calls == [primary]
