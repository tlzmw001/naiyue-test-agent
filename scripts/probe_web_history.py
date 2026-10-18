"""Offline TCP history/compaction probe using two real service processes."""

import argparse
import asyncio
import json
import logging
import socket
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from scripts.probe_web import ACCESS, _launch, _stop, _until


def _catalog(root):
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from naiyue_test_agent.model_access import ModelCatalog

    class HistoryModel(BaseChatModel):
        @property
        def _llm_type(self):
            return 'offline-history'

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, *args, **kwargs):
            raise AssertionError('Synchronous model invocation is forbidden')

        async def _reply(self, messages):
            summary = str(messages[0].content).startswith('Summarize completed')
            if summary:
                text = 'HISTORY_SUMMARY: earlier questions were answered.'
            else:
                prompt = next(m.content for m in reversed(messages) if isinstance(m, HumanMessage))
                if prompt == 'FOLLOW_AFTER_RESTART':
                    assert any(m.additional_kwargs.get('naiyue_compaction_summary') for m in messages)
                    assert not any('QUESTION_00' in str(m.content) for m in messages)
                    text = 'RESTARTED_WITH_SUMMARY'
                else:
                    assert prompt.startswith('QUESTION_')
                    text = 'ANSWER_' + prompt[9:11] + ' ' + 'x' * 30000
            with (root / 'model-calls.jsonl').open('a', encoding='utf-8') as output:
                output.write(json.dumps({'summary': summary}) + '\n')
            return AIMessage(id=uuid4().hex, content=text)

        async def _agenerate(self, messages, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=await self._reply(messages))])

        async def _astream(self, messages, **kwargs):
            response = await self._reply(messages)
            yield ChatGenerationChunk(message=AIMessageChunk(id=response.id, content=response.content))
            yield ChatGenerationChunk(message=AIMessageChunk(id=response.id, content='',
                response_metadata={'finish_reason': 'stop'}))

    model = HistoryModel(metadata={
        'naiyue_model_limits': {'context_window_tokens': 2000000, 'max_output_tokens': 8192},
        'naiyue_compaction_reserve': {'tokens': 2000},
        'naiyue_compaction_keep_recent': {'tokens': 1},
        'naiyue_compaction_summary_output': {'tokens': 256},
    })
    return ModelCatalog('offline', {'offline': model})


def _worker(args):
    import uvicorn
    from naiyue_test_agent import application
    from naiyue_test_agent.web.app import create_web_app
    from naiyue_test_agent.web.settings import WebSettings

    root = Path(args.worker)
    application.load_model_catalog = lambda _: _catalog(root)
    logging.basicConfig(level=logging.WARNING)
    assets = root / 'assets'
    assets.mkdir(exist_ok=True)
    (assets / 'index.html').write_text('<html>offline HTTP probe</html>', encoding='utf-8')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        settings = WebSettings(config_path='unused', database_path=root / 'sessions.sqlite',
            frontend_dist=assets, port=port, access_token=ACCESS, history_page_messages=3)
        app = create_web_app(settings)
        (root / 'ready.json').write_text(json.dumps({'port': port}), encoding='utf-8')
        try:
            uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='warning')).run(sockets=[listener])
        except KeyboardInterrupt:
            logging.getLogger(__name__).info('offline_history_worker_shutdown_signal')


def _pages(client, endpoint):
    rows, cursor, used = [], None, set()
    for _ in range(64):
        response = client.get(endpoint + '/messages', params={'before': cursor} if cursor else {})
        response.raise_for_status()
        assert len(response.content) <= 128 * 1024
        page = response.json()
        assert len(page['messages']) <= 3
        rows = page['messages'] + rows
        if not page['has_more']:
            assert page['next_cursor'] is None
            assert len({row['id'] for row in rows}) == len(rows)
            return rows
        cursor = page['next_cursor']
        assert cursor and cursor not in used
        used.add(cursor)
    raise AssertionError('History did not reach its beginning')


def _finished(client, response, deadline):
    response.raise_for_status()
    identity = response.json()['run_id']
    def state():
        value = client.get(f'/api/runs/{identity}')
        value.raise_for_status()
        view = value.json()
        return view if view['done'] else None
    view = _until(state, deadline)
    assert view['status'] == 'completed'
    return view


def _calls(root):
    return [json.loads(line) for line in (root / 'model-calls.jsonl').read_text().splitlines()]


def _parent(args):
    import httpx

    deadline = time.monotonic() + args.timeout_seconds
    with tempfile.TemporaryDirectory(prefix='naiyue-history-offline-') as directory:
        root = Path(directory)
        endpoint, original, before_close = None, None, None
        for phase in ('create', 'restore'):
            if phase == 'restore':
                (root / 'ready.json').unlink()
            process, output, port = _launch(root, deadline, worker_module='scripts.probe_web_history')
            origin = f'http://127.0.0.1:{port}'
            try:
                with httpx.Client(base_url=origin, headers={'Authorization': 'Bearer ' + ACCESS,
                        'Origin': origin}, timeout=min(10, args.timeout_seconds)) as client:
                    def ready():
                        try:
                            return client.get('/api/models').status_code == 200
                        except httpx.ConnectError:
                            return False
                    _until(ready, deadline)
                    if phase == 'create':
                        created = client.post('/api/sessions', json={'model_id': 'offline'})
                        created.raise_for_status()
                        endpoint = '/api/sessions/' + created.json()['session_id']
                        for n in range(18):
                            _finished(client, client.post(endpoint + '/runs', json={
                                'request_id': str(uuid4()), 'text': f'QUESTION_{n:02} ' + 'q' * 30000}), deadline)
                        original = _pages(client, endpoint)
                        assert len(original) == 36 and all(row['content_truncated'] for row in original)
                        first = client.get(endpoint + '/messages').json()
                        view = _finished(client, client.post(endpoint + '/compact', json={'request_id': str(uuid4())}), deadline)
                        assert view['compaction_result']['status'] == 'applied'
                        older = client.get(endpoint + '/messages', params={'before': first['next_cursor']})
                        older.raise_for_status()
                        assert older.json()['coverage']['upper'] == first['coverage']['upper']
                        before_close = _pages(client, endpoint)
                        assert before_close == original and len(_calls(root)) == 19
                    else:
                        client.post(endpoint + '/prepare').raise_for_status()
                        assert _pages(client, endpoint) == before_close
                        assert len(_calls(root)) == 19
                        _finished(client, client.post(endpoint + '/runs', json={
                            'request_id': str(uuid4()), 'text': 'FOLLOW_AFTER_RESTART'}), deadline)
                        after = _pages(client, endpoint)
                        assert after[:36] == original and len(after) == 38
                        assert after[-1]['content'] == 'RESTARTED_WITH_SUMMARY'
                        calls = _calls(root)
                        assert len(calls) == 20 and sum(row['summary'] for row in calls) == 1
            finally:
                _stop(process, output, deadline)
    print('web_history_probe=PASS mode=offline tcp=True large_history=True pages=complete '
          'compact=preserved two_processes=True model_context=summary read_replay=False model_calls=20')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout-seconds', type=float, default=60)
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 10 <= args.timeout_seconds <= 120:
        parser.error('timeout-seconds must be between 10 and 120')
    try:
        _worker(args) if args.worker else _parent(args)
        return 0
    except Exception as error:
        print(f'web_history_probe=FAIL error_type={type(error).__name__}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
