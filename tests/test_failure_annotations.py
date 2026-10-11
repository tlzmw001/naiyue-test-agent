"""Failure descriptions travel on the same exception without wrapping it."""

import asyncio

import pytest

from naiyue_test_agent.contracts.failures import (
    FailureDescription, attach_failure_description, read_failure_description,
)
from test_layered_failure_contract import VendorQuotaError, quota, build


@pytest.mark.parametrize('error', [TimeoutError('PRIVATE'), ValueError('PRIVATE'), asyncio.CancelledError()])
def test_annotation_preserves_exception_identity_and_cause(error):
    cause = RuntimeError('PRIVATE_CAUSE')
    error.__cause__ = cause
    original = (error.args, error.__cause__, error.__context__, error.__suppress_context__)
    desc = FailureDescription('model', 'transient', 'timeout', type(error).__name__)
    assert attach_failure_description(error, desc, run_id='run') is True
    assert read_failure_description(error, run_id='run') is desc
    assert (error.args, error.__cause__, error.__context__, error.__suppress_context__) == original
    assert read_failure_description(error, run_id='other') is None


def test_failed_setter_keeps_original_exception(caplog):
    class Refuses(RuntimeError):
        def __setattr__(self, name, value):
            if name == 'naiyue_failure':
                raise ValueError('PRIVATE_SETTER')
            super().__setattr__(name, value)
    error = Refuses('PRIVATE')
    desc = FailureDescription('model', 'unknown', 'unclassified', 'Refuses')
    assert attach_failure_description(error, desc, run_id='run') is False
    assert read_failure_description(error, run_id='run') is None
    assert 'PRIVATE_' not in caplog.text and 'annotation' in caplog.text


def test_unknown_is_valid_but_forged_annotation_is_rejected(caplog):
    error = TimeoutError('PRIVATE')
    desc = FailureDescription('model', 'unknown', 'unclassified', 'TimeoutError')
    attach_failure_description(error, desc, run_id='run')
    assert read_failure_description(error, run_id='run') is desc
    error.naiyue_failure = ('run', FailureDescription('model', 'unknown', 'unclassified', 'ValueError'))
    assert read_failure_description(error, run_id='run') is None
    error.naiyue_failure = 'PRIVATE_RAW'
    assert read_failure_description(error, run_id='run') is None
    assert 'PRIVATE_' not in caplog.text


def test_application_reads_annotation_and_classifies_model_once(monkeypatch):
    error = VendorQuotaError('PRIVATE')
    app, events, _catalog = build(monkeypatch, failure=error)
    calls = []
    def classify(error):
        calls.append(error)
        return quota(error)
    app.configure_error_classifiers(model_id='A', model=[classify])
    with pytest.raises(VendorQuotaError) as caught:
        app.run(app.create_session('A'), [('user', 'probe')])
    assert caught.value is error and calls == [error]
    assert read_failure_description(error, run_id=events[-1].run_id).reason == 'vendor_quota'
    assert events[-1].data.failure.reason == 'vendor_quota'
    assert not app._sessions._active_runs


def test_broken_annotation_getter_is_safe_and_does_not_search_cause(caplog):
    class BadGetter(RuntimeError):
        @property
        def naiyue_failure(self):
            raise ValueError('PRIVATE_GETTER')
    error = BadGetter('PRIVATE')
    assert read_failure_description(error, run_id='run') is None
    cause = TimeoutError('PRIVATE_CAUSE')
    desc = FailureDescription('model', 'transient', 'timeout', 'TimeoutError')
    attach_failure_description(cause, desc, run_id='run')
    outer = RuntimeError('PRIVATE_OUTER')
    outer.__cause__ = cause
    assert read_failure_description(outer, run_id='run') is None
    assert 'PRIVATE_' not in caplog.text


def test_annotation_does_not_add_wrapper_frames_or_change_typed_retry():
    from langchain.agents.middleware import ModelRetryMiddleware, ModelResponse
    from langchain_core.exceptions import ModelTimeoutError
    from langchain_core.messages import AIMessage
    import traceback
    error = ModelTimeoutError('PRIVATE')
    desc = FailureDescription('model', 'transient', 'timeout', 'ModelTimeoutError')
    attempts = []
    def call(request):
        attempts.append(request)
        if len(attempts) == 1:
            try:
                raise error
            except ModelTimeoutError:
                attach_failure_description(error, desc, run_id='run')
                raise
        return ModelResponse(result=[AIMessage(content='recovered')])
    retry = ModelRetryMiddleware(max_retries=1, retry_on=ModelTimeoutError,
                                initial_delay=0, jitter=False, on_failure='error')
    assert retry.wrap_model_call(object(), call).result[0].content == 'recovered'
    assert len(attempts) == 2
    assert all('annotation' not in frame.name for frame in traceback.extract_tb(error.__traceback__))
