"""One run's disposable presentation projection and bounded event replay."""

import asyncio
from collections import deque
from copy import deepcopy
from dataclasses import replace
import json
import logging
from threading import Lock

from naiyue_test_agent.application.conversation.stream_projection import StreamingMessageProjection
from naiyue_test_agent.tools.settings import ToolProgressSettings
from .errors import WebError
from .serialization import encoded_size, wire_event, project_event_for_web
from .agent_projection import observe_agent, summarize_agent
from .chat_history import recent_rows


logger = logging.getLogger(__name__)


class DeliveredRun:
    def __init__(self, session_id, rows, settings, on_started, *, progress_settings=None):
        self.session_id, self.settings = session_id, settings
        self.run_id = None
        self.conversation_revision = 0
        self.started = asyncio.get_running_loop().create_future()
        self.started.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        self.changed = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.lock = Lock()
        self.events = deque()
        self.event_bytes = 0
        self._evicted_through = 0
        self.projection = StreamingMessageProjection()
        self.projection.seed(session_id, rows)
        self.last_sequence = 0
        self.status = 'running'
        self.mode = 'message'
        self.compaction_result = None
        self.manual_review_required = False
        self.failure = None
        self.retained = []
        self.tool_states = {}
        self.agents = {}
        self.context_snapshot = None
        progress = progress_settings or ToolProgressSettings()
        self.progress_max_text_bytes = progress.max_text_bytes
        self.stop_requested = False
        self.done = False
        self.task = None
        self.unsubscribe = None
        self.wake_pending = False
        self.on_started = on_started
        self._registered = False

    def _wake(self):
        with self.lock:
            self.wake_pending = False
        self.changed.set()

    def _ready(self, run_id):
        if not self._registered:
            self.on_started(self)
            self._registered = True
        if not self.started.done():
            self.started.set_result(run_id)

    def observe(self, event):
        if event.session_id != self.session_id:
            return
        with self.lock:
            if self.run_id is None:
                self.run_id = event.run_id
                if event.event_type != 'run_started':
                    self.projection.incomplete = True
                self.loop.call_soon_threadsafe(self._ready, event.run_id)
            if self.run_id != event.run_id or event.sequence <= self.last_sequence:
                return
            if event.sequence != self.last_sequence + 1:
                self.projection.incomplete = True
            self.last_sequence = event.sequence
            try:
                event = project_event_for_web(event, progress_max_text_bytes=self.progress_max_text_bytes)
                payload = wire_event(event)
                payload_bytes = encoded_size(payload)
                if event.event_type == 'model_context_snapshot' and payload_bytes > self.settings.max_frame_bytes:
                    event = replace(event, data=replace(event.data, snapshot_json=None))
                    payload = wire_event(event)
                    payload_bytes = encoded_size(payload)
                    logger.warning('web_context_snapshot_omitted reason=view_limit')
                self.projection(event)
                if event.source is not None:
                    observe_agent(self.agents, payload, self.projection.messages)
                else:
                    if event.event_type == 'run_started':
                        self.mode = event.data.mode
                    if event.event_type == 'compaction_finished' and event.data.trigger == 'manual':
                        self.compaction_result = {'status': event.data.status, 'reason': event.data.reason}
                    self._observe_tool(payload)
                    if event.event_type == 'model_context_snapshot':
                        self.context_snapshot = json.loads(event.data.snapshot_json) if event.data.snapshot_json else None
                if (event.source is None and event.event_type == 'control_accepted' and event.data.kind == 'stop'
                        and self.status == 'running'):
                    self.stop_requested = True
                if payload_bytes > self.settings.max_frame_bytes:
                    # Transport gaps recover from the bounded view, like cache eviction.
                    self._evicted_through = max(self._evicted_through, event.sequence)
                    logger.warning('web_event_frame_omitted event_type=%s reason=frame_limit', event.event_type)
                else:
                    self._retain_event(payload, payload_bytes)
                if event.source is None and event.event_type == 'run_finished':
                    self.status = event.data.status
                    self.manual_review_required = event.data.manual_review_required
                    self.failure = payload['data']['failure']
                    self._mark_tools_unconfirmed(event.timestamp)
            except Exception as error:
                self.projection.incomplete = True
                logger.error('web_event_projection_failed error_type=%s', type(error).__name__)
            if not self.wake_pending:
                self.wake_pending = True
                self.loop.call_soon_threadsafe(self._wake)

    def _retain_event(self, payload, size):
        """Caller owns the lock; eviction never invalidates the live projection."""
        self.events.append(payload)
        self.event_bytes += size
        while len(self.events) > self.settings.event_capacity or self.event_bytes > self.settings.event_cache_bytes:
            removed = self.events.popleft()
            self.event_bytes -= encoded_size(removed)
            self._evicted_through = max(self._evicted_through, removed['sequence'])

    def _observe_tool(self, event):
        kind, data = event['event_type'], event['data']
        if kind not in ('tool_call_started', 'tool_call_finished', 'tool_progress'):
            return
        if self.status != 'running':
            return
        call_id = data['tool_call_id']
        old = self.tool_states.get(call_id, {})
        batch_id = next((row['id'] for row in reversed(self.projection.messages)
            if row['type'] == 'ai' and any(call.get('id') == call_id
                for call in row.get('tool_calls', []))), None)
        if kind == 'tool_progress':
            if (old.get('run_id') != self.run_id or old.get('state') != 'running'
                    or old.get('tool_name') != data['tool_name']
                    or old.get('batch_id') not in (None, data['batch_id'])):
                return
            self.tool_states[call_id] = {**old, **data, 'last_activity_at': event['timestamp']}
        elif kind == 'tool_call_started':
            self.tool_states[call_id] = {**data, 'run_id': self.run_id, 'batch_id': batch_id,
                'state': 'running', 'started_at': event['timestamp']}
        else:
            if old.get('run_id') != self.run_id or (batch_id is not None and old.get('batch_id') != batch_id):
                old = {}
            self.tool_states[call_id] = {**old, **data, 'run_id': self.run_id,
                'batch_id': batch_id or old.get('batch_id'), 'state': 'finished',
                'finished_at': event['timestamp']}

    def _mark_tools_unconfirmed(self, timestamp):
        rows = [*self.tool_states.values(), *(row for agent in self.agents.values() for row in agent['tool_states'].values())]
        for row in rows:
            if row['state'] == 'running':
                row.update(state='unconfirmed', finished_at=timestamp)

    def view(self):
        with self.lock:
            rows = recent_rows([row for row in self.projection.messages if not row.get('identity_only')], self.settings)
            batches = {row['id'] for row in rows if row['type'] == 'ai'} | {
                row.get('parent_message_id') for row in rows if row['type'] == 'tool'}
            value = {'session_id': self.session_id, 'conversation_revision': self.conversation_revision, 'run_id': self.run_id, 'status': self.status, 'mode': self.mode,
                'messages': rows, 'last_sequence': self.last_sequence,
                'incomplete': self.projection.incomplete, 'done': self.done,
                'manual_review_required': self.manual_review_required, 'failure': self.failure,
                'stop_requested': self.stop_requested,
                'retained': deepcopy(self.retained), 'tool_states': {
                    key: deepcopy({k: v for k, v in item.items() if k not in ('args', 'result')})
                    for key, item in self.tool_states.items() if item.get('batch_id') in batches or item['state'] == 'running'},
                'coverage': {'kind': 'window', 'first_message_id': rows[0]['id'] if rows else None},
                'context_snapshot': deepcopy(self.context_snapshot), 'compaction_result': deepcopy(self.compaction_result),
                'agents': {path: {**summarize_agent(agent, self.settings.max_frame_bytes // (4 * max(1, len(self.agents)))),
                                  'snapshot_sequence': self.last_sequence}
                    for path, agent in self.agents.items()}}
        if encoded_size(value) > self.settings.max_frame_bytes and value.get('context_snapshot') is not None:
            value['context_snapshot'] = None
            logger.warning('web_context_summary_omitted reason=view_limit')
        if encoded_size(value) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return value

    def agent_view(self, agent_path):
        """Full selected child data has its own size boundary, independent of root controls."""
        with self.lock:
            agent = self.agents.get(agent_path)
            if agent is None:
                return None
            value = {**deepcopy({key: item for key, item in agent.items() if key not in ('tool_states', 'messages')}),
                     'messages': recent_rows(agent['messages'], self.settings),
                     'tool_states': {identity: deepcopy({key: item for key, item in row.items() if key != 'result'})
                                     for identity, row in agent['tool_states'].items()}}
        if encoded_size(value) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return value

    def seed_data(self):
        with self.lock:
            rows = deepcopy(self.projection.messages[-self.settings.history_page_messages:])
            batches = {row['id'] for row in rows if row['type'] == 'ai'}
            states = {key: deepcopy(row) for key, row in self.tool_states.items() if row.get('batch_id') in batches}
            return rows, states

    def context_view(self):
        with self.lock:
            return deepcopy(self.context_snapshot)

    def _check_cursor(self, after):
        if after > self.last_sequence or self.projection.incomplete or after < self._evicted_through:
            raise WebError('resync_required')

    def check_cursor(self, after):
        with self.lock:
            self._check_cursor(after)

    async def stream(self, after):
        while True:
            with self.lock:
                try:
                    self._check_cursor(after)
                except WebError:
                    yield_gap = True
                    batch, done = [], True
                else:
                    yield_gap = False
                    batch = [e for e in self.events if e['sequence'] > after]
                    done = self.done
                    if not batch and not done:
                        self.changed.clear()
            if yield_gap:
                yield {'event': 'resync_required', 'data': {'code': 'resync_required'}}
                return
            for event in batch:
                after = event['sequence']
                yield {'event': 'runtime', 'id': str(after), 'data': event}
            if done:
                return
            if not batch:
                await self.changed.wait()
