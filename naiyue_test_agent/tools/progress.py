"""Bounded, cooperative native custom reports; no application/runtime imports."""

import logging
from time import monotonic

from langgraph.errors import GraphBubbleUp

from .settings import ToolProgressSettings


logger = logging.getLogger(__name__)


class ToolProgress:
    def __init__(self, runtime, settings=None):
        self.settings = settings if settings is not None else ToolProgressSettings()
        self._writer = None
        self._diagnosed = False
        self._pending = None
        self._last_sent = None
        self._stage = None
        try:
            context = getattr(runtime, 'context', None) or {}
            permit = context.get('_execution_permit', {})
            self._identity = {'batch_id': permit.get('batch_id'),
                'tool_call_id': getattr(runtime, 'tool_call_id', None), 'tool_name': permit.get('tool_name')}
            self._control = context.get('_run_control')
            writer = getattr(runtime, 'stream_writer', None)
            if (self.settings.enabled and callable(writer)
                    and all(isinstance(v, str) and v for v in self._identity.values())):
                self._writer = writer
        except GraphBubbleUp:
            raise
        except Exception as error:
            self._warn(error)

    def _warn(self, error):
        if not self._diagnosed:
            self._diagnosed = True
            logger.warning('tool_progress_unavailable error_type=%s', type(error).__name__)

    def report(self, *, phase, activity='running', completed=None, total=None, unit=None, text=''):
        if self._writer is None:
            return
        try:
            if (activity not in ('queued', 'preparing', 'running', 'stopping', 'settling')
                    or not isinstance(phase, str) or not 0 < len(phase) <= 64
                    or unit not in (None, 'bytes', 'lines', 'entries', 'matches')
                    or not isinstance(text, str)
                    or any(v is not None and (type(v) is not int or v < 0) for v in (completed, total))
                    or completed is not None and total is not None and completed > total):
                raise ValueError('Invalid progress report')
            if self._control is not None and self._control.requested and activity in ('running', 'preparing'):
                activity = 'stopping'
            text = text.encode('utf-8')[-self.settings.max_text_bytes:].decode('utf-8', errors='ignore')
            self._pending = {'kind': 'naiyue.tool_progress', 'version': 1, **self._identity,
                'activity': activity, 'phase': phase, 'completed': completed, 'total': total, 'unit': unit, 'text': text}
            now = monotonic()
            if (self._last_sent is None or self._stage != (activity, phase)
                    or now - self._last_sent >= self.settings.min_interval_milliseconds / 1000):
                self.flush()
        except GraphBubbleUp:
            raise
        except Exception as error:
            self._warn(error)

    def flush(self):
        if self._writer is None or self._pending is None:
            return
        payload, self._pending = self._pending, None
        try:
            self._writer(payload)
            self._last_sent = monotonic()
            self._stage = (payload['activity'], payload['phase'])
        except GraphBubbleUp:
            self._writer = None
            raise
        except Exception as error:
            self._writer = None
            self._warn(error)
        except BaseException:
            self._writer = None
            raise
