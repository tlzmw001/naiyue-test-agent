"""Bounded two-process persistence probe; default is deterministic and offline."""

import asyncio

import argparse

from naiyue_test_agent.config_paths import get_models_path
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


def _worker(args):
    return asyncio.run(_async__worker(args))

async def _async__worker(args):
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    logging.getLogger().setLevel(logging.CRITICAL)
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_persistent import open_async_persistent_application
    from naiyue_test_agent.application.session_storage import SessionStorage
    from naiyue_test_agent.model_access import ModelCatalog

    class HistoryModel(FakeMessagesListChatModel):
        def _generate(self, messages, *_positional, **kwargs):
            found = any(isinstance(m, HumanMessage) and args_value in str(m.content) for m in messages)
            answer = 'SAVED' if args.worker == 'save' else args_value if found else 'MISSING'
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content=answer, response_metadata={'finish_reason': 'stop'}))])

    args_value = args.value
    original = application._assemble_interactive_runtime
    calls = []
    def bounded_assemble(*positional, **options):
        record = options['record_selection']
        def bounded_record(*args):
            if calls:
                raise RuntimeError('Model call budget exceeded')
            calls.append(True)
            record(*args)
        options['record_selection'] = bounded_record
        return original(*positional, **options)

    with patch.object(application, '_assemble_interactive_runtime', bounded_assemble):
        with patch.object(application, 'load_model_catalog',
                          application.load_model_catalog if args.real else
                          lambda _p: ModelCatalog('offline', {'offline': HistoryModel(responses=[])})):
            async with open_async_persistent_application(args.config, database_path=args.database, tools=[],
                    system_prompt='Keep the synthetic test fact. Answer only the requested exact value.', response_format=None) as app:
                storage = app._sessions._persistence.storage
                if args.worker == 'save':
                    session = await app.acreate_session(app.default_model_id)
                    prompt = f'The synthetic test fact is {args.value}. Remember it and reply only SAVED.'
                else:
                    session = await app.arestore_session(args.session)
                    prompt = 'What is the synthetic test fact from the earlier conversation? Reply with only its exact value.'
                result = await app.arun(session, [('user', prompt)])
                row = storage.get(session)
                assert len(calls) == 1 and row['state'] == 'ready'
                history_used = result['messages'][-1].text.strip() == args.value if args.worker == 'resume' else None
                if args.worker == 'resume':
                    assert history_used
                print(json.dumps({'status': 'returned', 'session_id': session, 'thread_id': row['thread_id'],
                                  'run_id': row['run_id'], 'checkpoint_id': row['checkpoint_id'],
                                  'history_used': history_used}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--real', action='store_true')
    parser.add_argument('--config', default=str(get_models_path()))
    parser.add_argument('--worker', choices=['save', 'resume'], help=argparse.SUPPRESS)
    parser.add_argument('--session', help=argparse.SUPPRESS)
    parser.add_argument('--value', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        try:
            _worker(args)
        except Exception as error:
            from naiyue_test_agent.agent_runtime.events import safe_error_type
            print(json.dumps({'status': 'failed', 'error_type': safe_error_type(error)}), flush=True)
            return 1
        return 0
    deadline = time.monotonic() + 95
    value = secrets.token_hex(8)
    with tempfile.TemporaryDirectory(prefix='naiyue-session-probe-') as folder:
        database = args.database or Path(folder) / 'sessions.sqlite'
        results = []
        for mode in ['save', 'resume']:
            command = [sys.executable, '-m', 'scripts.probe_persistent_session', '--worker', mode,
                       '--database', str(database), '--value', value, '--config', args.config]
            if args.real:
                command.append('--real')
            if mode == 'resume':
                command.extend(['--session', results[0]['session_id']])
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print('persistent_session_probe=FAIL reason=deadline_exceeded', flush=True)
                return 1
            try:
                process = subprocess.run(command, capture_output=True, text=True,
                                         timeout=remaining)
                result = json.loads(process.stdout.strip())
            except (subprocess.TimeoutExpired, ValueError):
                print('persistent_session_probe=FAIL reason=timeout_or_invalid_result', flush=True)
                return 1
            if process.returncode != 0:
                print('persistent_session_probe=FAIL error_type=' + result.get('error_type', 'Unknown'), flush=True)
                return 1
            results.append(result)
        assert results[0]['session_id'] == results[1]['session_id']
        assert results[0]['thread_id'] == results[1]['thread_id']
        assert results[0]['run_id'] != results[1]['run_id']
        assert results[0]['checkpoint_id'] != results[1]['checkpoint_id']
        print(f'persistent_session_probe=PASS mode={"real" if args.real else "offline"} '
              'two_processes=True session_id=stable thread_id=stable run_id=new '
              'checkpoint=advanced history_used=True', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
