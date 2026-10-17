"""Classifiers use one protocol; protected facts bypass ordinary resolution."""

from native_application_helpers import async_test

import asyncio

import pytest

from naiyue_test_agent.contracts.failures import FailureDescription, normalize_classifiers, resolve_failure
from naiyue_test_agent.agent_runtime import errors as runtime_errors
from naiyue_test_agent.agent_runtime.errors import RuntimeErrorPolicy, ModelResponseRejected
from naiyue_test_agent.agent_runtime.run_control import UserStop
from naiyue_test_agent.tools.processes import ProcessCleanupError
from test_layered_failure_contract import VendorQuotaError, build
from naiyue_test_agent.model_access import errors as model_errors
from naiyue_test_agent.application.failure_policy import ApplicationErrorPolicy


def test_single_argument_classifier_is_rejected_without_invoking():
    calls = []

    def legacy(error):
        calls.append(error)
        return None

    with pytest.raises(ValueError, match='error and source'):
        normalize_classifiers([legacy])
    assert calls == []


def test_two_argument_classifier_is_kept_without_wrapper_or_invocation():
    calls = []

    def classify(error, source):
        calls.append((error, source))
        return None

    prepared = normalize_classifiers([classify])
    assert prepared == (classify,) and prepared[0] is classify
    assert calls == []
    error = RuntimeError('PRIVATE')
    assert prepared[0](error, 'tool') is None
    assert calls == [(error, 'tool')]


@async_test
async def test_wrong_classifier_arity_leaves_all_mappings_unchanged(monkeypatch):
    app, _events, catalog = await build(monkeypatch)

    def model(error, source):
        return FailureDescription(source, 'limit_exceeded', 'vendor_quota', type(error).__name__)

    def wrong(error):
        return None

    with pytest.raises(ValueError, match='error and source'):
        app.configure_error_classifiers(model_id='A', model=[model], runtime=[wrong])
    assert catalog.describe_failure('A', VendorQuotaError()).reason == 'unclassified'
    assert app._sessions._runtime_classifiers == ()


@pytest.mark.parametrize('error,source,category,reason', [
    (UserStop(), 'runtime', 'cancelled', 'user_stop'),
    (asyncio.CancelledError(), 'runtime', 'cancelled', 'task_cancelled'),
    (ProcessCleanupError('PRIVATE', facts={'process_reaped': False}),
     'tool', 'tool_outcome_unknown', 'tool_outcome_unknown'),
    (ProcessCleanupError('PRIVATE'), 'tool', 'tool_outcome_unknown', 'tool_outcome_unknown'),
    (ModelResponseRejected('invalid_tool_calls'), 'runtime', 'model_response_rejected', 'invalid_tool_calls'),
    (runtime_errors.ContextCompactionError('PRIVATE'), 'runtime', 'context_error', 'compaction_failed'),
    (runtime_errors.ToolBatchSchedulingError('PRIVATE'), 'runtime', 'runtime_error', 'tool_batch_scheduling_failed'),
])
def test_protected_rule_constructs_description_without_resolver_or_extensions(
    monkeypatch, error, source, category, reason,
):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Protected rules must not enter ordinary resolution')

    policy = RuntimeErrorPolicy(classifiers=[forbidden])
    monkeypatch.setattr(runtime_errors, 'resolve_failure', forbidden)
    result = policy.describe(error, source='model')
    assert result == FailureDescription(source, category, reason, type(error).__name__)
    assert 'PRIVATE' not in repr(result)


@pytest.mark.parametrize('diagnostic_failure', [False, True])
def test_invalid_protected_reason_keeps_original_and_safe_fallback(monkeypatch, caplog, diagnostic_failure):
    error = ModelResponseRejected('PRIVATE INVALID REASON')
    policy = RuntimeErrorPolicy()
    if diagnostic_failure:
        def fail(_error):
            raise TypeError('PRIVATE DIAGNOSTIC FAILURE')
        monkeypatch.setattr(RuntimeErrorPolicy, '_diagnostic', staticmethod(fail))
    with pytest.raises(ModelResponseRejected) as caught:
        try:
            raise error
        except ModelResponseRejected as original:
            result = policy.describe(original, source='model')
            assert result == FailureDescription('runtime', 'unknown', 'unclassified', 'ModelResponseRejected')
            raise
    assert caught.value is error
    assert 'PRIVATE' not in caplog.text
    expected = 'failure_diagnostic_report_failed' if diagnostic_failure else 'runtime_failure_diagnostics_unavailable'
    assert expected in caplog.text


def test_none_classifier_continues_to_next_rule():
    error = TimeoutError('PRIVATE_PRIMARY')
    expected = FailureDescription('model', 'transient', 'custom_timeout', 'TimeoutError')
    calls = []

    def skip(current, source):
        calls.append(('skip', current, source))
        return None

    def match(current, source):
        calls.append(('match', current, source))
        return expected

    result = model_errors.describe_model_failure(error, classifiers=[skip, match])
    assert result is expected
    assert calls == [('skip', error, 'model'), ('match', error, 'model')]


def test_none_classifier_continues_to_builtin():
    error = TimeoutError('PRIVATE_PRIMARY')
    calls = []

    def skip(current, source):
        calls.append((current, source))
        return None

    result = model_errors.describe_model_failure(error, classifiers=[skip])
    assert result == FailureDescription('model', 'transient', 'timeout', 'TimeoutError')
    assert calls == [(error, 'model')]


def test_valid_unknown_stops_later_rules_and_builtin(monkeypatch):
    error = TimeoutError('PRIVATE_PRIMARY')
    expected = FailureDescription('model', 'unknown', 'unclassified', 'TimeoutError')
    calls = []

    def match(current, source):
        calls.append((current, source))
        return expected

    def forbidden(*_args):
        raise AssertionError('A matched description must finish selection')

    monkeypatch.setattr(model_errors, 'classify_model_failure', forbidden)
    result = model_errors.describe_model_failure(error, classifiers=[match, forbidden])
    assert result is expected
    assert calls == [(error, 'model')]


def test_builtin_unknown_keeps_safe_type_without_raw_text():
    error = ValueError('PRIVATE_PRIMARY')
    result = model_errors.describe_model_failure(error)
    assert result == FailureDescription('model', 'unknown', 'unclassified', 'ValueError')
    assert 'PRIVATE' not in repr(result)
    assert error.args == ('PRIVATE_PRIMARY',)


def test_classifier_failure_returns_unknown_without_continuing(monkeypatch, caplog):
    error = TimeoutError('PRIVATE_PRIMARY')
    calls = []
    forbidden_calls = []

    def broken(current, source):
        calls.append((current, source))
        raise ValueError('PRIVATE_CLASSIFIER')

    def forbidden(*_args):
        forbidden_calls.append(_args)
        raise AssertionError('A classifier fault must not continue selection')

    monkeypatch.setattr(model_errors, 'classify_model_failure', forbidden)
    result = model_errors.describe_model_failure(error, classifiers=[broken, forbidden])
    assert result == FailureDescription('model', 'unknown', 'unclassified', 'TimeoutError')
    assert calls == [(error, 'model')]
    assert forbidden_calls == []
    assert 'model_failure_diagnostics_unavailable' in caplog.text
    assert 'PRIVATE' not in caplog.text


def test_invalid_description_returns_unknown_without_continuing(monkeypatch, caplog):
    error = TimeoutError('PRIVATE_PRIMARY')
    forbidden_calls = []

    def wrong_source(current, source):
        assert current is error and source == 'model'
        return FailureDescription('tool', 'transient', 'timeout', 'TimeoutError')

    def forbidden(*_args):
        forbidden_calls.append(_args)
        raise AssertionError('An invalid description must not continue selection')

    monkeypatch.setattr(model_errors, 'classify_model_failure', forbidden)
    result = model_errors.describe_model_failure(error, classifiers=[wrong_source, forbidden])
    assert result == FailureDescription('model', 'unknown', 'unclassified', 'TimeoutError')
    assert forbidden_calls == []
    assert 'model_failure_diagnostics_unavailable' in caplog.text
    assert 'PRIVATE' not in caplog.text


def test_diagnostic_report_failure_still_returns_unknown(caplog):
    error = TimeoutError('PRIVATE_PRIMARY')
    fault = ValueError('PRIVATE_CLASSIFIER')
    reports = []
    builtin_calls = []

    def broken_classifier(current, source):
        assert current is error and source == 'model'
        raise fault

    def builtin(current):
        builtin_calls.append(current)
        return model_errors.classify_model_failure(current)

    def broken_report(current):
        reports.append(current)
        raise RuntimeError('PRIVATE_REPORT')

    result = resolve_failure(error, source='model', classifiers=[broken_classifier],
        builtin=builtin, report_diagnostic=broken_report)
    assert result == FailureDescription('model', 'unknown', 'unclassified', 'TimeoutError')
    assert reports == [fault] and builtin_calls == []
    assert error.args == ('PRIVATE_PRIMARY',)
    assert 'failure_diagnostic_report_failed' in caplog.text
    assert 'PRIVATE' not in caplog.text


def test_application_no_match_returns_none():
    error = ValueError('PRIVATE_PRIMARY')
    policy = ApplicationErrorPolicy()
    result = policy.try_describe(error)
    assert result is None
    assert error.args == ('PRIVATE_PRIMARY',)
