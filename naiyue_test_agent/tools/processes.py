"""Own a host child, stream bytes with backpressure, and confirm resource closure."""

import asyncio
import logging
import math
import os
import signal
import subprocess
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from threading import Thread
from typing import Literal
from collections.abc import Callable

from .process_protocol import ProcessProtocol
from .settings import ProcessSettings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessRequest:
    """Native Host request; run authority is supplied by the process capability."""
    argv: list[str]
    cwd: Path
    env: dict[str, str]
    timeout_seconds: float | None = None
    merge_output: bool = False
    eof_policy: Literal['shell', 'complete'] = 'complete'
    execution_environment: dict[str, object] = field(default_factory=lambda: {'kind': 'host'})
    on_started: Callable[[], None] | None = field(default=None, compare=False, repr=False)
    on_execution_started: Callable[[float], None] | None = field(default=None, compare=False, repr=False)
    on_exited: Callable[[], None] | None = field(default=None, compare=False, repr=False)
    stdin_fd: int | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ProcessOutcome:
    returncode: int | None
    termination: Literal['exited', 'timeout', 'user_stop']
    output_complete: bool
    process_reaped: bool
    pipes_closed: bool
    consumer_settled: bool
    backpressure_count: int


class ProcessCleanupError(RuntimeError):
    """Necessary process or consumer settlement could not be confirmed."""

    def __init__(self, message='Process cleanup could not be confirmed.', *, facts=None, consumer_task=None):
        super().__init__(message)
        self.facts = facts or {}
        self.consumer_task = consumer_task


class HostProcessRunner:
    def __init__(self, settings=None):
        self.settings = settings or ProcessSettings()

    async def run(self, request, consume, control=None):
        if request.execution_environment.get('kind') != 'host':
            raise ValueError('Only host process execution is available')
        seconds = request.timeout_seconds
        if (not request.argv or request.eof_policy not in ('shell', 'complete') or
                request.stdin_fd is not None and (type(request.stdin_fd) is not int or request.stdin_fd < 0) or
                seconds is not None and (type(seconds) not in (int, float) or
                    not math.isfinite(seconds) or seconds <= 0)):
            raise ValueError('Invalid process request')
        if control is not None and control.requested:
            return ProcessOutcome(None, 'user_stop', True, True, True, True, 0)
        loop = asyncio.get_running_loop()
        origin = loop.time()
        deadline = origin + seconds if seconds is not None else None
        if request.on_execution_started is not None:
            request.on_execution_started(origin)
        protocol = ProcessProtocol(self.settings.max_pending_output_bytes,
                                   merge_output=request.merge_output, on_exited=request.on_exited)
        options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
                   else {'start_new_session': True})
        spawn = asyncio.create_task(loop.subprocess_exec(lambda: protocol, *request.argv,
            cwd=request.cwd, env=request.env, stdin=request.stdin_fd if request.stdin_fd is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if request.merge_output else subprocess.PIPE, **options))
        consumer = None
        primary = None
        termination = 'exited'
        try:
            # Shield creation: cancellation still leaves us responsible for the eventual handle.
            termination = await self._await_spawn(spawn, control, deadline)
            if termination == 'ready':
                if request.on_started is not None:
                    request.on_started()
                consumer = asyncio.create_task(protocol.deliver(consume))
                termination = await self._watch(protocol, consumer, request, control, deadline)
        except BaseException as error:
            primary = error

        cleanup = asyncio.create_task(self._settle(spawn, protocol, consumer, consume,
            terminate=primary is not None or termination != 'exited'))
        cancellation = primary if isinstance(primary, asyncio.CancelledError) else None
        while True:
            try:
                consumer_error = await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
                if cleanup.cancelled():
                    raise ProcessCleanupError(facts={'cleanup_cancelled': True}) from cancellation
            except ProcessCleanupError as error:
                raise error from (cancellation or primary or error.__cause__)
        if cancellation is not None:
            raise cancellation
        if primary is not None:
            raise primary
        spawn.result()  # A late acquisition failure must retain its own exception.
        if consumer_error is not None:
            raise consumer_error
        if protocol.errors:
            raise protocol.errors[0]
        return ProcessOutcome(protocol.transport.get_returncode(), termination,
            protocol.output_complete, True, protocol.pipes_closed, True, protocol.backpressure_count)

    async def _await_spawn(self, spawn, control, deadline):
        loop = asyncio.get_running_loop()
        while True:
            if spawn.done():
                spawn.result()
                return 'ready'
            if control is not None and control.requested:
                return 'user_stop'
            now = loop.time()
            if deadline is not None and now >= deadline:
                return 'timeout'
            interval = 0.05 if deadline is None else min(0.05, deadline - now)
            # wait does not cancel the owned acquisition on timeout/caller cancellation.
            await asyncio.wait((spawn,), timeout=interval)

    async def _watch(self, protocol, consumer, request, control, deadline):
        loop = asyncio.get_running_loop()
        while True:
            protocol.changed.clear()
            if consumer.done():
                consumer.result()
            if protocol.errors:
                raise protocol.errors[0]
            if control is not None and control.requested:
                return 'user_stop'
            exited = protocol.transport.get_returncode() is not None
            if exited and protocol.pipes_closed and consumer.done():
                return 'exited'
            now = loop.time()
            if deadline is not None and now >= deadline:
                return 'timeout'
            quiet = self.settings.shell_exit_quiet_milliseconds / 1000
            if (exited and request.eof_policy == 'shell' and protocol.pending_bytes == 0
                    and now - protocol.last_output >= quiet):
                protocol.close_pipes()
            try:
                interval = 0.05 if deadline is None else min(0.05, deadline - now)
                await asyncio.wait_for(protocol.changed.wait(), interval)
            except TimeoutError:
                continue

    async def _settle(self, spawn, protocol, consumer, consume, *, terminate):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.cleanup_timeout_seconds

        async def remaining(awaitable):
            return await asyncio.wait_for(asyncio.shield(awaitable), max(0, deadline - loop.time()))

        termination_requested = False
        try:
            # A public spawn may have delivered its transport before returning.
            if terminate and protocol.transport is not None and protocol.transport.get_returncode() is None:
                await asyncio.wait_for(self._terminate(protocol.transport), max(0, deadline - loop.time()))
                termination_requested = True
            await remaining(spawn)
            transport = protocol.transport
            if consumer is None:
                consumer = asyncio.create_task(protocol.deliver(consume))
            if terminate and not termination_requested and transport.get_returncode() is None:
                await asyncio.wait_for(self._terminate(transport), max(0, deadline - loop.time()))
            while transport.get_returncode() is None:
                protocol.changed.clear()
                if transport.get_returncode() is None:
                    await remaining(protocol.changed.wait())
            # Allow ready EOF callbacks before closing held descendant pipes.
            await asyncio.sleep(0)
            if not protocol.pipes_closed:
                protocol.close_pipes()
            consumer_error = None
            try:
                await remaining(consumer)
            except Exception as error:
                if not consumer.done():
                    raise
                consumer_error = error
            while not protocol.closed:
                protocol.changed.clear()
                if not protocol.closed:
                    await remaining(protocol.changed.wait())
            transport.close()
            return consumer_error
        except BaseException as error:
            transport = protocol.transport
            if transport is None and spawn.done() and not spawn.cancelled():
                # Public start failure without any acquired handle.
                if spawn.exception() is not None:
                    return None
            if transport is not None:
                try:
                    protocol.close_pipes()
                except Exception as close_error:
                    logger.error('process_pipe_close_failed error_type=%s', type(close_error).__name__)
            if not spawn.done():
                # The custody deadline expired: ask public spawn to unwind its own
                # acquisition too; report uncertainty rather than releasing success.
                spawn.cancel()
                spawn.add_done_callback(self._report_late_consumer)
            settled = consumer is None or consumer.done()
            if not settled:
                consumer.add_done_callback(self._report_late_consumer)
            facts = {'spawn_settled': spawn.done(),
                'process_reaped': transport is not None and transport.get_returncode() is not None,
                'pipes_closed': protocol.pipes_closed, 'consumer_settled': settled,
                'transport_closed': protocol.closed}
            logger.error('process_cleanup_unconfirmed facts=%s error_type=%s', facts, type(error).__name__)
            raise ProcessCleanupError(facts=facts, consumer_task=consumer if not settled else None) from error

    @staticmethod
    def _report_late_consumer(task):
        if task.cancelled():
            logger.error('process_consumer_cancelled_after_cleanup_failure')
        elif task.exception() is not None:
            logger.error('process_consumer_failed_after_cleanup_failure error_type=%s', type(task.exception()).__name__)

    @staticmethod
    async def _terminate(transport):
        if os.name != 'nt':
            try:
                os.killpg(transport.get_pid(), signal.SIGKILL)
            except ProcessLookupError:
                pass
            return
        # Public asyncio transport on Windows; no global event-loop policy changes.
        loop = asyncio.get_running_loop()
        protocol = ProcessProtocol(1, merge_output=True)
        killer, _ = await loop.subprocess_exec(lambda: protocol,
            'taskkill', '/PID', str(transport.get_pid()), '/T', '/F',
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            while killer.get_returncode() is None:
                protocol.changed.clear()
                if killer.get_returncode() is None:
                    await protocol.changed.wait()
            if killer.get_returncode() != 0 and transport.get_returncode() is None:
                raise OSError('Process tree termination failed')
        finally:
            if killer.get_returncode() is None:
                try:
                    killer.kill()
                except ProcessLookupError:
                    pass
            killer.close()


def run_async_sync(async_call):
    """A per-call bridge, including synchronous graphs invoked inside an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(async_call())
    result = Future()
    def execute():
        try:
            result.set_result(asyncio.run(async_call()))
        except BaseException as error:
            result.set_exception(error)
    worker = Thread(target=execute, name='naiyue-process-sync')
    worker.start()
    try:
        return result.result()
    finally:
        worker.join()
