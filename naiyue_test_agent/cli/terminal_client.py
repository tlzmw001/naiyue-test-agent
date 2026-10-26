"""Synchronous terminal access to a shared service, owning only a network loop."""
import asyncio
from contextlib import aclosing
from concurrent.futures import CancelledError
from hashlib import sha256
import json
import logging
import re
from threading import Event, Thread, current_thread
from urllib.parse import urlencode
from uuid import uuid4

from naiyue_test_agent.service.launcher import ensure_service
from naiyue_test_agent.service.ownership import read_discovery
from naiyue_test_agent.service.settings import ServiceError, ServiceIdentity
from .terminal_events import event_fact

logger = logging.getLogger(__name__)


class TerminalClient:
    def __init__(self, options):
        self.options, self.scope_id = options, None
        self._closed, self._temporary, self.disconnecting = False, False, False
        self._ready, self._streams = Event(), {}
        self._cursors, self._observed_messages = {}, {}
        self._watcher, self._lease = None, None
        self._display = None
        self._thread = Thread(target=self._serve, name='naiyue-terminal-network')
        self._thread.start()
        self._ready.wait()
        try:
            self._http = self._submit(lambda: ensure_service(options))
        except BaseException:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            raise

    def _serve(self):
        with asyncio.Runner() as runner:
            self._loop = runner.get_loop()
            self._ready.set()
            self._loop.run_forever()

    def _submit(self, operation):
        if current_thread() is self._thread:
            raise RuntimeError('Terminal dispatch cannot wait on its owning event loop')
        if self._closed:
            raise RuntimeError('Terminal client is closed')
        async def execute():
            return await operation()
        try:
            return asyncio.run_coroutine_threadsafe(execute(), self._loop).result()
        except CancelledError:
            raise ServiceError('client_disconnected') from None

    def bind(self, identity):
        if identity != 'chats' and re.fullmatch(r'(?:temp-)?[0-9a-f-]{36}', identity) is None:
            raise ServiceError('invalid_service_path')
        self.scope_id = identity

    def _path(self, suffix):
        if suffix.startswith('/api/v1/'):
            return suffix
        if self.scope_id is None:
            raise ServiceError('scope_required')
        return f'/api/v1/scopes/{self.scope_id}' + suffix

    def request(self, method, suffix, *, json=None, request_id=None):
        return self._submit(lambda: self._read(self._path(suffix)) if method.upper() == 'GET' else
            self._http.request(method, self._path(suffix), json=json, request_id=request_id))

    def lease(self, root):
        ticket = self.request('POST', '/api/v1/temporary', json={'root': str(root) if root else None})
        self.bind(ticket['scope_id'])
        self._temporary = True
        async def open_lease():
            ready = self._loop.create_future()
            async def hold():
                try:
                    path = f'/api/v1/temporary/{self.scope_id}/lease'
                    async with self._http._http.stream('GET', path, timeout=None) as response:
                        if response.status_code != 200:
                            await self._http._json(response)
                        kind = None
                        async for line in response.aiter_lines():
                            if line.startswith('event:'):
                                kind = line[6:].strip()
                            if line.startswith('data:'):
                                if kind == 'error':
                                    raise ServiceError(json.loads(line[5:])['error']['code'])
                                if not ready.done():
                                    ready.set_result(None)
                except BaseException as error:
                    if not ready.done():
                        ready.set_exception(error)
                    elif not self._closed and not isinstance(error, asyncio.CancelledError):
                        logger.error('terminal_lease_lost error_type=%s', type(error).__name__)
                    raise
                finally:
                    if not ready.done():
                        ready.set_exception(ServiceError('temporary_lease_unavailable'))
            self._lease = asyncio.create_task(hold())
            await ready
        self._submit(open_lease)

    async def _history(self, session_id, *, database_path=None):
        prefix = self._path(f'/terminal/sessions/{session_id}') if database_path is None else '/api/v1/terminal/legacy'
        base = {} if database_path is None else {'database_path': str(database_path), 'session_id': session_id}
        value = await self._read(prefix + '/history' + ('?' + urlencode(base) if base else ''))
        messages = []
        for index in range(value['message_count']):
            offset, pieces, digest = 0, [], None
            while True:
                query = {**base, 'index': index, 'offset': offset}
                if value['checkpoint_id']:
                    query['checkpoint'] = value['checkpoint_id']
                part = await self._read(prefix + '/message?' + urlencode(query))
                if digest is not None and digest != part['sha256']:
                    raise ServiceError('history_cursor_stale')
                digest = part['sha256']
                pieces.append(part['text'])
                if part['complete']:
                    break
                if part['next_offset'] <= offset:
                    raise ServiceError('service_response_invalid')
                offset = part['next_offset']
            text = ''.join(pieces)
            if sha256(text.encode()).hexdigest() != digest:
                raise ServiceError('service_response_invalid')
            messages.append(json.loads(text))
        return {**value, 'messages': messages}

    def history(self, session_id):
        return self._submit(lambda: self._history(session_id))

    def legacy_history(self, database_path, session_id):
        return self._submit(lambda: self._history(session_id, database_path=database_path))

    def run(self, session_id, text):
        before = self.history(session_id)['messages']
        receipt = self.request('POST', f'/sessions/{session_id}/runs',
            json={'request_id': str(uuid4()), 'text': text})
        return self.wait(session_id, receipt['run_id'], before=before)

    async def _retry_read(self, error, attempt):
        if error.code != 'service_connection_failed' or attempt >= self.options.max_read_reconnects:
            raise error
        logger.warning('terminal_read_reconnecting code=%s', error.code)
        await asyncio.sleep(self.options.session_poll_interval)
        record = read_discovery(self.options)
        if record is None or record.instance_id != self._http.instance_id:
            raise ServiceError('service_instance_mismatch')
        actual = await self._http.request('POST', '/api/v1/handshake',
            json=self.options.identity.model_dump(mode='json'))
        self.options.identity.check(ServiceIdentity.model_validate(actual['identity']))
        if actual['instance_id'] != self._http.instance_id:
            raise ServiceError('service_instance_mismatch')

    async def _read(self, path):
        attempt = 0
        while True:
            try:
                return await self._http.request('GET', path)
            except ServiceError as error:
                await self._retry_read(error, attempt)
                attempt += 1

    def wait(self, session_id, run_id, *, before=()):
        async def wait():
            result = await asyncio.shield(self._stream(session_id, run_id))
            old = {row['id']: row for row in before}
            return {**result, 'reply_messages': [row for row in result['messages']
                if row['type'] == 'ai' and not row.get('is_context_summary') and row != old.get(row['id'])]}
        return self._submit(wait)

    def _stream(self, session_id, run_id):
        old = self._streams.get(run_id)
        if old is not None and old.done() and (old.cancelled() or old.exception() is not None):
            del self._streams[run_id]
        if run_id not in self._streams:
            task = asyncio.create_task(self._consume(session_id, run_id))
            task.add_done_callback(lambda done: self._stream_finished(run_id, done))
            self._streams[run_id] = task
        return self._streams[run_id]

    def _stream_finished(self, run_id, task):
        self._observed_done(task)
        # Waiters already hold their Task. Keep only the latest finished result
        # alongside active streams, rather than each round's entire history.
        for identity, saved in tuple(self._streams.items()):
            if identity != run_id and saved.done():
                del self._streams[identity]
                self._cursors.pop(identity, None)

    def _observed_done(self, task):
        if not task.cancelled() and task.exception() is not None:
            error = task.exception()
            logger.error('terminal_observation_failed error_type=%s', type(error).__name__)
            if self._display is not None and not self.disconnecting:
                try:
                    self._display.write('连接或观察未确认，请重新连接当前会话核对状态；不会重发执行请求。')
                except Exception as error:
                    logger.warning('terminal_display_failed error_type=%s', type(error).__name__)

    async def _consume(self, session_id, run_id):
        cursor, attempt, poll_only = self._cursors.get(run_id, 0), 0, False
        baseline = dict(self._observed_messages)
        if self._display:
            self._display.begin_call()
        while True:
            gap = False
            if not poll_only:
                try:
                    async with aclosing(self._http.events(self.scope_id, run_id, after=cursor)) as stream:
                        async for frame in stream:
                            if frame.get('event') == 'resync_required':
                                gap = True
                                break
                            try:
                                event = event_fact(frame['data'], session_id, run_id, cursor)
                            except (ValueError, TypeError, KeyError, AttributeError):
                                raise ServiceError('service_event_invalid') from None
                            cursor = self._cursors[run_id] = event.sequence
                            attempt = 0
                            if self._display:
                                try:
                                    self._display(event)
                                except Exception as error:
                                    logger.warning('terminal_display_failed error_type=%s', type(error).__name__)
                except ServiceError as error:
                    if error.code in ('service_event_invalid', 'service_response_invalid', 'service_response_too_large'):
                        logger.warning('terminal_event_fallback code=%s', error.code)
                        poll_only = True
                    elif error.code == 'resync_required':
                        logger.warning('terminal_replay_expired')
                        gap = True
                    else:
                        await self._retry_read(error, attempt)
                        attempt += 1
                        continue
            view = await self._read(self._path(f'/runs/{run_id}'))
            if view['done']:
                break
            # Only an explicit replay gap authorizes skipping missing facts.
            # Ordinary EOF resumes at the last fact actually rendered.
            if gap:
                cursor = self._cursors[run_id] = max(cursor, view.get('last_sequence', cursor))
            await asyncio.sleep(self.options.session_poll_interval)
        value = await self._history(session_id)
        result = {**value, 'run': view, 'reply_messages': [row for row in value['messages']
            if row['type'] == 'ai' and not row.get('is_context_summary') and row != baseline.get(row['id'])]}
        self._observed_messages = {row['id']: row for row in value['messages']}
        if self._display:
            try:
                self._display.finish_view(result)
            except Exception as error:
                logger.warning('terminal_display_failed error_type=%s', type(error).__name__)
        return result

    async def _watch_session(self, session_id, seen):
        while True:
            value = await self._read(self._path(f'/sessions/{session_id}'))
            live = value.get('live')
            if live and live['run_id'] != seen:
                self._stream(session_id, live['run_id'])
                seen = live['run_id']
            await asyncio.sleep(self.options.session_poll_interval)

    def watch(self, session_id, display):
        self._display = display
        async def start():
            initial = await self._read(self._path(f'/sessions/{session_id}'))
            live = initial.get('live')
            self._observed_messages = ({row['id']: row for row in (await self._history(session_id))['messages']}
                                       if not live or live['done'] else {})
            if live and not live['done']:
                self._stream(session_id, live['run_id'])
            self._watcher = asyncio.create_task(self._watch_session(session_id, live['run_id'] if live else None))
            self._watcher.add_done_callback(self._observed_done)
        self._submit(start)

    def pending_approvals(self, session_id):
        return self.request('GET', f'/sessions/{session_id}')['approval_batches']

    def active_run(self, session_id):
        value = self.request('GET', f'/sessions/{session_id}')
        live = value.get('live')
        return live['run_id'] if live and not live['done'] else None

    def control(self, session_id, kind, *, text=None, agent_path='root'):
        run_id = self.active_run(session_id)
        if run_id is None:
            raise ServiceError('stale_run')
        body = {'request_id': str(uuid4()), 'kind': kind, 'agent_path': agent_path}
        if text is not None:
            body['text'] = text
        return self.request('POST', f'/runs/{run_id}/controls', json=body)

    def approve(self, session_id, submission, *, stop=False):
        before = self.history(session_id)['messages']
        path = f'/sessions/{session_id}/approvals' + ('/stop' if stop else '')
        receipt = self.request('POST', path, json={'request_id': str(uuid4()), **submission})
        return {**receipt, 'terminal_before': before}

    def close(self):
        if self._closed:
            return
        self.disconnecting = True
        async def close():
            primary = None
            try:
                if self._temporary:
                    await self._http.request('DELETE', f'/api/v1/temporary/{self.scope_id}')
            except BaseException as error:
                primary = error
            async def close_readers():
                tasks = [t for t in (self._watcher, self._lease, *self._streams.values()) if t is not None]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            for cleanup in (close_readers, self._http.aclose):
                try:
                    await cleanup()
                except BaseException as error:
                    if primary is None:
                        primary = error
                    else:
                        logger.error('terminal_close_secondary_failed error_type=%s', type(error).__name__)
            self._streams.clear()
            self._cursors.clear()
            self._observed_messages.clear()
            self._watcher = self._lease = self._display = None
            if primary is not None:
                raise primary
        try:
            self._submit(close)
        finally:
            self._closed = True
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
