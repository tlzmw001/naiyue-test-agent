"""Optional per-execution HTTP facts; SDKs retain all network ownership."""

import logging
from contextvars import ContextVar
from contextlib import contextmanager
from threading import RLock
from uuid import uuid4
from weakref import WeakSet, WeakMethod, ref

from naiyue_test_agent.contracts.failures import safe_error_type
from naiyue_test_agent.contracts.timing import start_timer, elapsed_ms


logger = logging.getLogger(__name__)
_CURRENT = ContextVar('naiyue_model_http_call', default=None)
_MARKER = 'naiyue_model_http_attempt'


class _ClientToken:
    def __init__(self):
        self.calls, self.lock = WeakSet(), RLock()
        self.closed = False
        self.request_hook = self.response_hook = None

    def add(self, call):
        with self.lock:
            if self.closed:
                return False
            self.calls.add(call)
            return True

    def remove(self, call):
        with self.lock:
            self.calls.discard(call)

    def close(self):
        with self.lock:
            self.closed = True
            calls = tuple(self.calls)
            self.calls.clear()
        for call in calls:
            try:
                call.close(incomplete=True)
            except Exception as error:
                call.release()
                logger.warning('model_http_observation_failed error_type=%s', safe_error_type(error))


class HttpCallObservation:
    """Callback-owned finite state shared by inherited task/thread contexts."""

    def __init__(self, invocation_id, emit, *, limit, tokens):
        self.invocation_id, self._emit = invocation_id, emit
        self._tokens = tuple(tokens)
        self._available = bool(tokens) and type(limit) is int and limit > 0
        self._enabled, self._incomplete = self._available, False
        self._limit, self._pending, self._lock = limit, {}, RLock()
        self.closed = False
        parent = _CURRENT.get()
        self._run_gap = parent._run_gap if isinstance(parent, HttpCallObservation) else parent
        if self._available:
            for token in self._tokens:
                if not token.add(self):
                    self._incomplete, self._enabled = True, False

    @property
    def pending_count(self):
        with self._lock:
            return len(self._pending)

    def bind(self):
        previous = current_http_call()
        if previous is not None and previous is not self:
            previous.fail('ambiguous_context')
            previous._enabled = False
            self.fail('ambiguous_context')
            self._enabled = False
        _CURRENT.set(self)

    def fail(self, reason):
        with self._lock:
            self._incomplete = True
        logger.warning('model_http_capture_incomplete reason=%s', reason)
        if isinstance(self._run_gap, WeakMethod):
            report = self._run_gap()
            if report is not None:
                report()

    def _send(self, *args):
        try:
            self._emit(*args)
        except Exception as error:
            self._incomplete = True
            logger.warning('model_http_observation_failed error_type=%s', safe_error_type(error))

    def begin(self, request, token):
        with self._lock:
            if self.closed or not self._enabled:
                return
            if token not in self._tokens:
                self.fail('client_mismatch')
                return
            if len(self._pending) >= self._limit:
                self.fail('attempt_limit')
                return
            identity = uuid4().hex
            self._pending[identity] = start_timer()
            request.extensions[_MARKER] = (ref(self), identity, token)
            self._send('started', identity, None, None, None)

    def response(self, identity, token, status):
        with self._lock:
            if self.closed or token not in self._tokens or identity not in self._pending:
                return
            started = self._pending.pop(identity)
            self._send('finished', identity, 'response_headers', status, elapsed_ms(started))

    def close(self, *, incomplete=False):
        with self._lock:
            if not self.closed:
                self.closed = True
                self._incomplete |= incomplete
                try:
                    for identity in self._pending:
                        self._send('finished', identity, 'unconfirmed', None, None)
                finally:
                    self.release()
            return 'incomplete' if self._incomplete else 'complete' if self._available else 'unavailable'

    def release(self):
        """Release references even when closing observation itself failed."""
        with self._lock:
            self.closed = True
            self._pending.clear()
            tokens, self._tokens = self._tokens, ()
            self._emit = None
        for token in tokens:
            token.remove(self)


def current_http_call():
    call = _CURRENT.get()
    return call if isinstance(call, HttpCallObservation) and not call.closed else None


@contextmanager
def bind_http_run(report_gap):
    token = _CURRENT.set(WeakMethod(report_gap))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def report_http_gap():
    scope = _CURRENT.get()
    target = scope._run_gap if isinstance(scope, HttpCallObservation) else scope
    if isinstance(target, WeakMethod):
        report = target()
        if report is not None:
            report()


def _request(request, token):
    call = None
    try:
        request.extensions.pop(_MARKER, None)
        call = current_http_call()
        if call is None:
            report_http_gap()
            logger.warning('model_http_context_unavailable')
        else:
            call.begin(request, token)
    except Exception as error:
        report_http_gap()
        if call is not None:
            call.fail('request_hook')
        logger.warning('model_http_observation_failed error_type=%s', safe_error_type(error))


def _response(response, token):
    call = None
    try:
        marker = response.request.extensions.get(_MARKER)
        if marker is None:
            call = current_http_call()
            if call is not None and call._enabled:
                call.fail('missing_attempt')
            return
        owner, identity, origin = marker
        call = owner()
        if call is not None and origin is token:
            call.response(identity, token, response.status_code)
    except Exception as error:
        if call is not None:
            call.fail('response_hook')
        logger.warning('model_http_observation_failed error_type=%s', safe_error_type(error))


def install_http_hooks(client, *, asynchronous):
    try:
        token = getattr(client, '_naiyue_http_observation', None)
        if token is None:
            token = _ClientToken()
            if asynchronous:
                async def request_hook(request):
                    _request(request, token)
                async def response_hook(response):
                    _response(response, token)
            else:
                def request_hook(request):
                    _request(request, token)
                def response_hook(response):
                    _response(response, token)
            token.request_hook, token.response_hook = request_hook, response_hook
            client._naiyue_http_observation = token
        for kind, hook in (('request', token.request_hook), ('response', token.response_hook)):
            if hook not in client.event_hooks[kind]:
                client.event_hooks[kind].append(hook)
        return token
    except Exception as error:
        report_http_gap()
        logger.warning('model_http_observation_failed error_type=%s', safe_error_type(error))
        return None


def client_token(client):
    token = getattr(client, '_naiyue_http_observation', None)
    if (token is not None and not token.closed
            and token.request_hook in client.event_hooks['request']
            and token.response_hook in client.event_hooks['response']):
        return token
    return None


def close_http_observation(client):
    try:
        token = getattr(client, '_naiyue_http_observation', None)
        if token is not None:
            token.close()
    except Exception as error:
        logger.warning('model_http_observation_failed error_type=%s', safe_error_type(error))
