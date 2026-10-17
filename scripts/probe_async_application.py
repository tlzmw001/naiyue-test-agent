"""Bounded two-process async stream/tool/control/persistence probe."""

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import argparse

from naiyue_test_agent.config_paths import get_models_path
import asyncio
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from uuid import uuid4


def _offline_catalog():
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from naiyue_test_agent.model_access import ModelCatalog

    class OfflineModel(BaseChatModel):
        @property
        def _llm_type(self):
            return 'offline-native-stream'

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, *args, **kwargs):
            raise AssertionError('Unexpected synchronous model request')

        def _reply(self, messages):
            value = next((m.content for m in reversed(messages) if isinstance(m, ToolMessage)), None)
            if value is None:
                return AIMessage(content='Calling the probe.', tool_calls=[
                    {'name': 'read_probe_value', 'args': {}, 'id': uuid4().hex}],
                    response_metadata={'finish_reason': 'tool_calls'})
            return AIMessage(content=value, response_metadata={'finish_reason': 'stop'})

        async def _agenerate(self, messages, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

        async def _astream(self, messages, **kwargs):
            response = self._reply(messages)
            identity = uuid4().hex
            for text in (response.content[:len(response.content) // 2], response.content[len(response.content) // 2:]):
                if text:
                    yield ChatGenerationChunk(message=AIMessageChunk(content=text, id=identity))
            chunks = [{'name': c['name'], 'args': '{}', 'id': c['id'], 'index': i}
                      for i, c in enumerate(response.tool_calls)]
            yield ChatGenerationChunk(message=AIMessageChunk(content='', id=identity, tool_call_chunks=chunks,
                                                            response_metadata=response.response_metadata))

    return ModelCatalog('offline', {'offline': OfflineModel()})


def _write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(value, output)


async def _worker(args):
    from langchain.tools import tool
    from langchain_core.callbacks import AsyncCallbackHandler
    from langchain_core.messages import AIMessage
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_persistent import open_async_persistent_application
    from naiyue_test_agent.application.stream_projection import StreamingMessageProjection

    if args.offline:
        application.load_model_catalog = lambda _: _offline_catalog()
    record_path = Path(args.handoff)
    expected = uuid4().hex if args.phase == 'start' else None
    holder, tool_calls = {}, []

    @tool
    async def read_probe_value() -> str:
        """Read one random synthetic value; no files or external effects."""
        tool_calls.append(True)
        if len(tool_calls) > 1:
            raise RuntimeError('Probe tool budget exceeded')
        holder['app'].steer(holder['session'],
            'The tool has returned. Do not call tools again. Reply only with its exact value.')
        return expected

    class Budget(AsyncCallbackHandler):
        raise_error = True

        def __init__(self, limit):
            self.limit, self.count = limit, 0

        async def on_chat_model_start(self, serialized, messages, **kwargs):
            self.count += 1
            if self.count > self.limit:
                raise RuntimeError('Probe model budget exceeded')

    budget = Budget(2 if args.phase == 'start' else 1)
    tools = [ToolSpec(read_probe_value, read_probe_value.args_schema, permissions=ToolPermission(default='allow'))]
    async with asyncio.timeout(args.timeout_seconds):
        async with open_async_persistent_application(args.config, database_path=args.database,
                tools=tools, system_prompt='Return only the exact requested synthetic value.', response_format=None) as app:
            app._agent = app._agent.with_config({'callbacks': [budget], 'recursion_limit': 40})
            if args.phase == 'start':
                session = await app.acreate_session(app.default_model_id)
                prompt = 'Call read_probe_value exactly once. Then reply only with the exact value returned by it.'
            else:
                saved = json.loads(record_path.read_text())
                expected = saved['value']
                session = await app.arestore_session(saved['session_id'])
                await app.aset_active_tools(session, [])
                restored = await app.aget_messages(session)
                assert any(m.content == expected for m in restored)
                prompt = 'Without calling any tools, reply only with the exact value read by read_probe_value earlier.'
            holder.update(app=app, session=session)
            events, projection = [], StreamingMessageProjection()
            async for event in app.astream_run(session, [('user', prompt)]):
                events.append(event)
                projection(event)
                if event.event_type == 'run_finished':
                    rows = await app.alist_sessions()
                    assert rows[0]['state'] == 'ready' and rows[0]['last_run_status'] == 'completed'
            messages = await app.aget_messages(session)
            assert isinstance(messages[-1], AIMessage) and messages[-1].content.strip() == expected
            assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'completed'
            final = messages[-1]
            deltas = [e for e in events if e.event_type == 'message_delta' and e.data.message_id == final.id]
            snapshot = next(e for e in events if e.event_type == 'message_completed' and e.data.message_id == final.id)
            assert deltas and max(e.sequence for e in deltas) < snapshot.sequence
            assert ''.join(e.data.text for e in deltas).strip() == expected
            visible = [row for row in projection.messages if row['id'] == final.id]
            assert len(visible) == 1 and visible[0]['content'].strip() == expected and not visible[0]['provisional']
            row = (await app.alist_sessions())[0]
            if args.phase == 'start':
                assert len(tool_calls) == 1 and budget.count == 2
                controls = [e for e in events if e.event_type == 'control_delivered']
                assert len(controls) == 1 and controls[0].data.kind == 'steering'
                _write_private(record_path, {'value': expected, 'session_id': session,
                    'thread_id': row['thread_id'], 'run_id': row['run_id'], 'checkpoint_id': row['checkpoint_id']})
            else:
                assert tool_calls == [] and budget.count == 1
                assert row['thread_id'] == saved['thread_id']
                assert row['run_id'] != saved['run_id'] and row['checkpoint_id'] != saved['checkpoint_id']
    print(f'async_application_worker=PASS phase={args.phase}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(get_models_path()))
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--timeout-seconds', type=float, default=180)
    parser.add_argument('--phase', choices=['start', 'resume'], help=argparse.SUPPRESS)
    parser.add_argument('--database', help=argparse.SUPPRESS)
    parser.add_argument('--handoff', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        parser.error('--timeout-seconds must be positive and finite')
    if args.phase:
        logging.disable(logging.CRITICAL)
        try:
            asyncio.run(_worker(args))
        except BaseException as error:
            from naiyue_test_agent.agent_runtime.events import safe_error_type
            print(f'async_application_worker=FAIL phase={args.phase} error_type={safe_error_type(error)}', flush=True)
            return 1
        return 0
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='naiyue-async-probe-') as directory:
        for phase in ('start', 'resume'):
            remaining = args.timeout_seconds - (time.monotonic() - started)
            if remaining <= 2:
                print('async_application_probe=FAIL reason=deadline_exceeded')
                return 1
            command = [sys.executable, '-m', 'scripts.probe_async_application', '--phase', phase,
                '--config', args.config, '--database', str(Path(directory) / 'sessions.sqlite'),
                '--handoff', str(Path(directory) / 'handoff.json'), '--timeout-seconds', str(remaining - 1)]
            if args.offline:
                command.append('--offline')
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=remaining)
            except subprocess.TimeoutExpired:
                print('async_application_probe=FAIL reason=deadline_exceeded')
                return 1
            if result.returncode != 0 or result.stdout.strip() != f'async_application_worker=PASS phase={phase}':
                print(f'async_application_probe=FAIL phase={phase} reason=worker_failed')
                return 1
    mode = 'offline' if args.offline else 'real'
    print(f'async_application_probe=PASS mode={mode} two_processes=True delta=verified tool=1 '
        'steering=verified terminal_after_save=verified history_used=True '
        'session_id=stable thread_id=stable run_id=new checkpoint=advanced')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
