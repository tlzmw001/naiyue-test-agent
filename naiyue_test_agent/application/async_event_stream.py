"""Bounded, thread-safe event delivery into an async consumer."""

import asyncio
from collections import deque
from threading import Lock


class EventStreamOverflow(RuntimeError):
    """The consumer must resynchronize instead of silently losing events."""


class AsyncEventStream:
    def __init__(self, subscribe, session_id, run_id, capacity=256):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError('Event capacity must be a positive integer')
        self._loop = asyncio.get_running_loop()
        self._ready = asyncio.Event()
        self._lock = Lock()
        self._items = deque()
        self._capacity = capacity
        self._closed = False
        self._overflow = False
        self._wake_pending = False
        self._session_id, self._run_id = session_id, run_id
        self._unsubscribe = subscribe(self)

    def _wake(self):
        with self._lock:
            self._wake_pending = False
        self._ready.set()

    def _notify_locked(self):
        if not self._wake_pending:
            self._wake_pending = True
            self._loop.call_soon_threadsafe(self._wake)

    def __call__(self, event):
        if event.session_id != self._session_id or event.run_id != self._run_id:
            return
        with self._lock:
            if self._closed:
                return
            if len(self._items) >= self._capacity:
                self._overflow = True
                self._closed = True
                self._items.clear()
            else:
                self._items.append(event)
            self._notify_locked()

    def finish(self):
        with self._lock:
            self._closed = True
            self._notify_locked()

    def close(self):
        self._unsubscribe()
        with self._lock:
            self._closed = True
            self._items.clear()
            self._notify_locked()

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            with self._lock:
                if self._overflow:
                    raise EventStreamOverflow('Event stream overflowed; resynchronize from application state.')
                if self._items:
                    return self._items.popleft()
                if self._closed:
                    raise StopAsyncIteration
                self._ready.clear()
            await self._ready.wait()
