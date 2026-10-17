"""One bounded synthetic model request; cancel after observing a real text delta."""

import argparse

from naiyue_test_agent.config_paths import get_models_path
import asyncio
import logging
import os
from pathlib import Path
import subprocess
import sys


def _offline_catalog():
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk
    from naiyue_test_agent.model_access import ModelCatalog

    class WaitingModel(BaseChatModel):
        @property
        def _llm_type(self):
            return 'offline-cancellation-probe'

        def _generate(self, *args, **kwargs):
            raise AssertionError('Synchronous IO is forbidden')

        async def _astream(self, *args, **kwargs):
            yield ChatGenerationChunk(message=AIMessageChunk(content='partial', id='probe-message'))
            await asyncio.Event().wait()

    return ModelCatalog('offline', {'offline': WaitingModel()})


async def _worker(args):
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_application import build_async_local_application

    if args.offline:
        application.load_model_catalog = lambda _: _offline_catalog()
    app = await build_async_local_application(args.config, tools=[], system_prompt='', response_format=None)
    events, calls, delta = [], [], asyncio.Event()

    def observe(event):
        events.append(event)
        if event.event_type == 'model_call_started':
            calls.append(event)
        elif event.event_type == 'message_delta':
            delta.set()

    app.subscribe_events(observe)
    session = await app.acreate_session(app.default_model_id)
    task = asyncio.create_task(app.arun(session, [('user',
        'Produce the integers from 1 to 10000, one per line. Begin immediately and do not explain.')]))
    try:
        async with asyncio.timeout(args.timeout_seconds):
            await delta.wait()
            app.request_stop(session)
            result = await task
            snapshot = await app._agent.aget_state({'configurable': {
                'thread_id': app._sessions._sessions[session]['thread_id']}})
            assert len(calls) == 1 and result['run_stopped']
            assert not any(m.type == 'ai' for m in result['messages'])
            assert not snapshot.next and not any(t.error or t.interrupts for t in snapshot.tasks)
            assert events[-1].data.status == 'stopped'
            assert events[-1].data.manual_review_required is False
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await app.aclose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=get_models_path())
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--timeout-seconds', type=int, default=120)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_seconds <= 300:
        parser.error('timeout must be from 1 to 300 seconds')
    if args.worker:
        logging.disable(logging.CRITICAL)
        os.environ['LANGSMITH_TRACING'] = 'false'
        os.environ['LANGCHAIN_TRACING_V2'] = 'false'
        try:
            asyncio.run(_worker(args))
        except BaseException as error:
            print(f'active_stop_worker=FAIL error_type={type(error).__name__}')
            return 1
        print('active_stop_worker=PASS')
        return 0
    command = [sys.executable, '-m', 'scripts.probe_active_stop', '--worker', '--config', str(args.config),
               '--timeout-seconds', str(args.timeout_seconds)]
    if args.offline:
        command.append('--offline')
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout_seconds + 5)
    except subprocess.TimeoutExpired:
        print('active_stop_probe=FAIL reason=deadline_exceeded')
        return 1
    if result.returncode or result.stdout.strip() != 'active_stop_worker=PASS':
        print('active_stop_probe=FAIL reason=worker_failed')
        return 1
    mode = 'offline' if args.offline else 'real'
    print(f'active_stop_probe=PASS mode={mode} logical_calls=1 delta=observed '
          'stopped=verified fake_answer=absent checkpoint=safe')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
