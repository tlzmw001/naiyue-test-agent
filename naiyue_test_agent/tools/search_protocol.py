"""Bounded native-search framing and diagnostics; no permission decisions."""

import asyncio
import json
import os

from .processes import ProcessRequest
from .permissions import execution_environment


def safe_text(value):
    escaped = json.dumps(value, ensure_ascii=False)[1:-1]
    return escaped.encode('utf-8', errors='backslashreplace').decode('utf-8')


class RecordBuffer:
    def __init__(self, separator, max_bytes):
        self.separator = separator
        self.max_bytes = max_bytes
        self.buffer = bytearray()
        self.discarding = False

    def feed(self, chunk):
        start = 0
        while start < len(chunk):
            end = chunk.find(self.separator, start)
            part = chunk[start:] if end < 0 else chunk[start:end]
            if not self.discarding:
                if len(self.buffer) + len(part) > self.max_bytes:
                    self.buffer.clear()
                    self.discarding = True
                else:
                    self.buffer.extend(part)
            if end < 0:
                return
            record = None if self.discarding else bytes(self.buffer)
            self.buffer.clear()
            self.discarding = False
            yield record
            start = end + len(self.separator)

    def finish(self):
        if self.buffer or self.discarding:
            raise ValueError('incomplete_record')


class DiagnosticTail:
    def __init__(self, max_bytes):
        self.max_bytes = max_bytes
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        if len(self.buffer) > self.max_bytes:
            del self.buffer[:-self.max_bytes]


class SkippedTargets:
    def __init__(self, max_bytes):
        self.max_bytes = max_bytes
        self.items = []
        self.size = 0
        self.count = 0

    def add(self, item):
        self.count += 1
        item = safe_text(item)
        size = len(item.encode()) + 2
        if self.size + size <= self.max_bytes:
            self.items.append(item)
            self.size += size

    def render(self, tool_name):
        if not self.count:
            return ''
        text = '; '.join(sorted(self.items))
        omitted = self.count - len(self.items)
        if omitted:
            text += f'; {omitted} additional targets omitted from this report'
        return f'\n[{tool_name}: not searched: {text}]'


class SearchStopped(Exception):
    """Stop only this owned search; never set the run's global stop signal."""


def check_search(deadline, control):
    if control is not None and control.requested:
        raise SearchStopped('stopped by the user')
    if deadline is not None:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise SearchStopped('timeout')
        return remaining
    return None


def inside_git(directory):
    return any((ancestor / '.git').exists() for ancestor in (directory, *directory.parents))


def argument_bytes(argv):
    if os.name == 'nt':
        import subprocess
        return len(subprocess.list2cmdline(argv).encode('utf-16-le')) + 2
    return sum(len(os.fsencode(arg)) + 1 for arg in argv)


async def stream_records(runner, argv, cwd, deadline, control, parser, on_record, diagnostics):
    remaining = check_search(deadline, control)
    request = ProcessRequest(argv, cwd, dict(os.environ),
        remaining, eof_policy='complete',
        execution_environment=execution_environment())
    async def consume(stream, chunk):
        if stream == 'stderr':
            diagnostics.feed(chunk)
        else:
            for record in parser.feed(chunk):
                check_search(deadline, control)
                await on_record(record)
    # Isolate process wait_for's transient cancel/uncancel from the owning
    # search task. External cancellation still propagates into this owned task.
    outcome = await asyncio.create_task(runner.run(request, consume, control))
    if outcome.termination != 'exited':
        raise SearchStopped('timeout' if outcome.termination == 'timeout' else 'stopped by the user')
    if not outcome.output_complete:
        raise ValueError('incomplete_record')
    parser.finish()
    return outcome
