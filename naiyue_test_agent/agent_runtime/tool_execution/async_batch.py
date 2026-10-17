"""Run-local async ordering and finite parallelism for a single tool batch."""

import asyncio
import logging

from langchain_core.messages import ToolMessage


logger = logging.getLogger(__name__)


class AsyncBatchPlan:
    def __init__(self, call_ids, sequential, run_id, error_type):
        self.call_ids = tuple(call_ids)
        self.sequential = sequential
        self.run_id = run_id
        self.error_type = error_type
        self.position = 0
        self.failed = False
        self.condition = asyncio.Condition()
        self.semaphore = None

    def _skipped(self, call_id, name):
        logger.error('tool_batch_skipped', extra={'tool_batch_event': {
            'event': 'tool_batch_skipped', 'tool_call_id': call_id,
            'tool_name': name, 'run_id': self.run_id,
        }})
        return ToolMessage(content='Tool was not executed because an earlier call in this batch failed.',
                           tool_call_id=call_id, name=name, status='error')

    async def execute(self, call_id, name, handler, limit):
        if call_id not in self.call_ids:
            raise self.error_type('Tool call is outside the current batch.')
        if type(limit) is not int or limit <= 0:
            raise ValueError('Tool concurrency must be a positive integer')
        if not self.sequential:
            if self.semaphore is None:
                self.semaphore = asyncio.Semaphore(limit)
            async with self.semaphore:
                if self.failed:
                    return self._skipped(call_id, name)
                try:
                    return await handler()
                except BaseException:
                    self.failed = True
                    raise

        index = self.call_ids.index(call_id)
        async with self.condition:
            if index < self.position:
                raise self.error_type('Tool call already completed.')
            try:
                await self.condition.wait_for(lambda: self.failed or self.position == index)
            except BaseException:
                self.failed = True
                self.condition.notify_all()
                raise
            if self.failed:
                return self._skipped(call_id, name)
        try:
            result = await handler()
        except BaseException:
            async with self.condition:
                self.failed = True
                self.condition.notify_all()
            raise
        async with self.condition:
            self.position += 1
            self.condition.notify_all()
        return result
