"""Owned attempts share one deadline, retry allowance and per-target cooldowns."""

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import errno
import logging
import math
import random
import socket
import ssl
from time import monotonic

import httpx

from .control import check_tool_stop
from .retry_settings import ToolRetrySettings


logger = logging.getLogger(__name__)
RETRYABLE_HTTP_STATUS = frozenset((429, 500, 502, 503, 504))


async def await_attempt(call, seconds, control):
    """Cancel and settle an owned task before timeout/stop can start another."""
    check_tool_stop(control, 'External tool request stopped; no further attempts were started.')
    task = asyncio.create_task(call())
    try:
        async with asyncio.timeout(seconds or None):
            while not task.done():
                check_tool_stop(control, 'External tool request stopped; no further attempts were started.')
                await asyncio.wait((task,), timeout=0.05)
            check_tool_stop(control, 'External tool request stopped; no further attempts were started.')
            return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def is_transient_error(error):
    if isinstance(error, BaseExceptionGroup):
        return bool(error.exceptions) and all(is_transient_error(item) for item in error.exceptions)
    chain, current = set(), error
    while current is not None and id(current) not in chain:
        chain.add(id(current))
        if isinstance(current, ssl.SSLError):
            return False
        current = current.__cause__ or current.__context__
    import anyio
    import httpx2
    if isinstance(error, (httpx.ProxyError, httpx2.ProxyError)):
        return False
    if isinstance(error, (TimeoutError, ConnectionError, EOFError,
            httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError,
            httpx2.NetworkError, httpx2.TimeoutException, httpx2.RemoteProtocolError,
            anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError)):
        return True
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    return isinstance(error, OSError) and error.errno in {
        errno.ECONNREFUSED, errno.ECONNRESET, errno.EHOSTUNREACH,
        errno.ENETUNREACH, errno.ETIMEDOUT, errno.EPIPE}


def retry_after_seconds(headers):
    value = headers.get('retry-after', headers.get('Retry-After'))
    if value is None:
        return 0
    try:
        if value.isascii() and value.isdecimal():
            seconds = float(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        if not math.isfinite(seconds):
            raise ValueError('Invalid retry delay')
        return max(0, seconds)
    except (TypeError, ValueError, OverflowError, AttributeError):
        logger.warning('tool_retry_after_invalid')
        return 0


class RetryBudget:
    def __init__(self, settings, tool_name, total_seconds, *, control=None, progress=None):
        self.settings = settings if settings is not None else ToolRetrySettings()
        self.tool_name, self.control, self.progress = tool_name, control, progress
        self.enabled = self.settings.applies(tool_name)
        self.deadline = monotonic() + total_seconds if total_seconds else None
        self.retries = 0
        self._failures, self._ready = {}, {}

    def remaining(self):
        if self.deadline is None:
            return 0
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError('External tool total deadline reached')
        return remaining

    def attempt_seconds(self, limit=None):
        limits = [seconds for seconds in (self.remaining(), limit) if seconds]
        if self.enabled:
            limits.append(self.settings.attempt_timeout_seconds)
        return min(limits) if limits else 0

    async def attempt(self, call, *, limit=None):
        result = await await_attempt(call, self.attempt_seconds(limit), self.control)
        self.remaining()
        return result

    def schedule(self, target, *, retry_after=0):
        count = self._failures.get(target, 0)
        self._failures[target] = count + 1
        initial, factor, maximum = (self.settings.initial_delay_seconds,
            self.settings.backoff_factor, self.settings.max_delay_seconds)
        if initial == 0 or maximum == 0:
            upper = 0
        elif initial >= maximum or factor == 1:
            upper = min(initial, maximum)
        else:
            logarithm = math.log(initial) + count * math.log(factor)
            upper = maximum if logarithm >= math.log(maximum) else math.exp(logarithm)
        self._ready[target] = monotonic() + max(random.uniform(0, upper), retry_after)

    def next_candidate(self, targets):
        self.remaining()
        if not self.enabled or self.retries >= self.settings.max_retries:
            return None
        eligible = [target for target in targets if target in self._ready
                    and (self.deadline is None or self._ready[target] < self.deadline)]
        return min(eligible, key=self._ready.get) if eligible else None

    async def claim_retry(self, target):
        check_tool_stop(self.control, 'External tool request stopped; no further attempts were started.')
        self.remaining()
        if self.next_candidate([target]) is None:
            return False
        delay = max(0, self._ready[target] - monotonic())
        if self.progress is not None:
            self.progress.report(phase='retry_wait', activity='queued',
                text=f'{target}: retry {self.retries + 1}/{self.settings.max_retries}')
        logger.info('tool_retry_wait tool=%s target=%s retry=%s delay_seconds=%.3f',
                    self.tool_name, target, self.retries + 1, delay)
        if delay:
            await await_attempt(lambda: asyncio.sleep(delay), self.remaining(), self.control)
        self.remaining()
        check_tool_stop(self.control, 'External tool request stopped; no further attempts were started.')
        self.retries += 1
        return True
