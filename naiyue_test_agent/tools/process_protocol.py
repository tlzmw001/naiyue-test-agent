"""Public asyncio subprocess callbacks; byte delivery never performs disk IO."""

import asyncio
from collections import deque


class ProcessProtocol(asyncio.SubprocessProtocol):
    def __init__(self, max_pending_bytes, *, merge_output=False, on_exited=None):
        self.on_exited = on_exited
        self.transport = None
        self.changed = asyncio.Event()
        self.output_ready = asyncio.Event()
        self.queue = deque()
        self.pending_bytes = 0
        self.max_pending_bytes = max_pending_bytes
        self.expected_pipes = {1} if merge_output else {1, 2}
        self.closed_pipes = set()
        self.natural_eof = set()
        self.forced_pipes = set()
        self.errors = []
        self.closed = False
        self.paused = False
        self.backpressure_count = 0
        self.last_output = asyncio.get_running_loop().time()

    def connection_made(self, transport):
        self.transport = transport
        self.changed.set()

    def pipe_data_received(self, fd, data):
        self.last_output = asyncio.get_running_loop().time()
        self.queue.append(('stdout' if fd == 1 else 'stderr', data))
        self.pending_bytes += len(data)
        if self.pending_bytes >= self.max_pending_bytes and not self.paused:
            self.paused = True
            self.backpressure_count += 1
            try:
                for number in self.expected_pipes - self.closed_pipes:
                    self.transport.get_pipe_transport(number).pause_reading()
            except Exception as error:
                self.errors.append(error)
        self.output_ready.set()
        self.changed.set()

    def consumed(self, size):
        self.pending_bytes -= size
        if self.paused and self.pending_bytes <= self.max_pending_bytes // 2:
            self.paused = False
            for number in self.expected_pipes - self.closed_pipes:
                self.transport.get_pipe_transport(number).resume_reading()
        self.changed.set()

    def process_exited(self):
        if self.on_exited is not None:
            self.on_exited()
        self.changed.set()

    def pipe_connection_lost(self, fd, exc):
        if fd in self.expected_pipes:
            self.closed_pipes.add(fd)
            if exc is None and fd not in self.forced_pipes:
                self.natural_eof.add(fd)
        if exc is not None:
            self.errors.append(exc)
        self.output_ready.set()
        self.changed.set()

    def connection_lost(self, exc):
        self.closed = True
        if exc is not None:
            self.errors.append(exc)
        self.output_ready.set()
        self.changed.set()

    @property
    def pipes_closed(self):
        return self.expected_pipes <= self.closed_pipes

    @property
    def output_complete(self):
        return self.expected_pipes <= self.natural_eof and not self.errors

    def close_pipes(self):
        for fd in self.expected_pipes - self.closed_pipes:
            self.forced_pipes.add(fd)
            self.transport.get_pipe_transport(fd).close()

    async def deliver(self, consume):
        try:
            while True:
                self.output_ready.clear()
                while self.queue:
                    stream, data = self.queue.popleft()
                    try:
                        await consume(stream, data)
                    finally:
                        self.consumed(len(data))
                if self.pipes_closed:
                    return
                await self.output_ready.wait()
        finally:
            self.changed.set()
