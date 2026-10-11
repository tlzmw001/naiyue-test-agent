"""Classify failures observed at the model-call boundary."""

import re
import logging

import anthropic
import httpx
from ollama import ResponseError
from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError
from langchain_core.exceptions import (
    ContextOverflowError, ModelAuthenticationError, ModelPermissionDeniedError,
    ModelInvalidRequestError, ModelNotFoundError, ModelRateLimitError,
    ModelAPIError, ModelTimeoutError, ModelConnectionError,
)
from naiyue_test_agent.contracts.failures import (
    resolve_failure, safe_error_type,
)


logger = logging.getLogger(__name__)


def input_context_overflow_reason(error: Exception) -> str | None:
    """Recognize explicit input rejection without exposing provider error text."""
    if isinstance(error, ContextOverflowError):
        return 'framework_context_overflow'
    if not isinstance(error, (APIStatusError, anthropic.APIStatusError)):
        return None
    if error.status_code != 400:
        return None
    if isinstance(error, APIStatusError) and error.code == 'context_length_exceeded':
        return 'context_length_exceeded'
    message = error.message
    if not isinstance(message, str) or re.search(r'rate limit|too many requests', message, re.I):
        return None
    if isinstance(error, APIStatusError) and re.search(
        r'\binput exceeds the context window\b', message, re.I,
    ):
        return 'input_exceeds_context_window'
    if isinstance(error, anthropic.APIStatusError) and re.search(r'\bprompt is too long\b', message, re.I):
        return 'prompt_too_long'
    return None


def classify_model_failure(error: Exception) -> tuple[str, str]:
    """Classify model request failures into fixed category and reason codes."""
    if input_context_overflow_reason(error) is not None:
        return 'request_rejected', 'context_window_exceeded'
    for error_type, category, reason in (
        (ModelAuthenticationError, 'access_denied', 'authentication_failed'),
        (ModelPermissionDeniedError, 'access_denied', 'permission_denied'),
        (ModelNotFoundError, 'request_rejected', 'not_found'),
        (ModelRateLimitError, 'limit_exceeded', 'limit_unspecified'),
        (ModelAPIError, 'transient', 'server_error'),
        (ModelTimeoutError, 'transient', 'timeout'),
        (ModelConnectionError, 'transient', 'connection_error'),
        (ModelInvalidRequestError, 'request_rejected', 'bad_request'),
    ):
        if isinstance(error, error_type):
            return category, reason
    if isinstance(error, (AuthenticationError, anthropic.AuthenticationError)):
        return 'access_denied', 'authentication_failed'
    if isinstance(error, anthropic.PermissionDeniedError):
        return 'access_denied', 'permission_denied'
    if isinstance(error, anthropic.NotFoundError):
        return 'request_rejected', 'not_found'
    if isinstance(error, anthropic.RateLimitError):
        return 'limit_exceeded', 'limit_unspecified'
    if isinstance(error, (APIStatusError, anthropic.APIStatusError)) and error.status_code >= 500:
        return 'transient', 'server_error'
    if isinstance(error, ResponseError):
        status = error.status_code
        if status == 400:
            return 'request_rejected', 'bad_request'
        if status == 401:
            return 'access_denied', 'authentication_failed'
        if status == 403:
            return 'access_denied', 'permission_denied'
        if status == 404:
            return 'request_rejected', 'not_found'
        if status == 429:
            return 'limit_exceeded', 'limit_unspecified'
        if status >= 500:
            return 'transient', 'server_error'
    if isinstance(error, (APITimeoutError, anthropic.APITimeoutError,
                          httpx.TimeoutException, TimeoutError)):
        return 'transient', 'timeout'
    if isinstance(error, (APIConnectionError, anthropic.APIConnectionError, ConnectionError)):
        return 'transient', 'connection_error'
    return 'unknown', 'unclassified'


def describe_model_failure(error: BaseException, *, classifiers=()):
    """Model-owned normalization; no agent, session, event, or recovery policy."""
    return resolve_failure(error, source='model', classifiers=classifiers, builtin=classify_model_failure,
        report_diagnostic=lambda fault: logger.error(
            'model_failure_diagnostics_unavailable error_type=%s', safe_error_type(fault)))


def unknown_failure_diagnostics(error: Exception) -> dict[str, str | int]:
    """Return only safe identifiers for an unclassified model exception."""
    details: dict[str, str | int] = {'error_type': safe_error_type(error)}
    if isinstance(error, (APIStatusError, anthropic.APIStatusError, ResponseError)):
        status_code = error.status_code
        if type(status_code) is int and 100 <= status_code <= 599:
            details['status_code'] = status_code
    return details
