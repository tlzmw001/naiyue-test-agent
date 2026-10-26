"""Offline Web tool/approval/TCP probe; browser fixtures use the same production UI."""

import argparse
from scripts.web_probe_app import command_headers
import asyncio
import json
import logging
import os
from pathlib import Path
import socket
import tempfile
import time
from uuid import uuid4

from .probe_web import ACCESS, _launch, _stop, _until


def _catalog(root):
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGenerationChunk
    from naiyue_test_agent.model_access import ModelCatalog

    class ToolModel(BaseChatModel):
        @property
        def _llm_type(self):
            return 'offline-web-tools-fixture'

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, *args, **kwargs):
            raise AssertionError('Synchronous invocation is forbidden')

        async def _astream(self, messages, **kwargs):
            humans = [m.content for m in messages if isinstance(m, HumanMessage)]
            results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
            identity = uuid4().hex
            if len(humans) > 1:
                assert {'external-one', 'external-two', 'listing'} <= results.keys()
                text = 'TOOL HISTORY RESTORED'
            elif results:
                assert results['external-one'].content == 'Approved external fixture content.\n'
                assert results['external-two'].status == 'error'
                assert 'Rejected external fixture content.' not in results['external-two'].content
                assert 'sample.txt' in results['listing'].content
                text = '离线验证完成：第一条读取已批准，第二条已拒绝，工作区目录已列出。'
            else:
                text = '正在核对文件，工作区外的两条读取需要你的审批。'
                yield ChatGenerationChunk(message=AIMessageChunk(content=text, id=identity))
                requests = [('read', {'path': str(root / 'outside-first.txt')}, 'external-one'),
                            ('read', {'path': str(root / 'outside-second.txt')}, 'external-two'),
                            ('ls', {'path': '.'}, 'listing')]
                yield ChatGenerationChunk(message=AIMessageChunk(content='', id=identity,
                    tool_call_chunks=[{'name': name, 'args': json.dumps(args), 'id': cid, 'index': i}
                        for i, (name, args, cid) in enumerate(requests)], response_metadata={'finish_reason': 'tool_calls'}))
                return
            yield ChatGenerationChunk(message=AIMessageChunk(content=text, id=identity))
            yield ChatGenerationChunk(message=AIMessageChunk(content='', id=identity, response_metadata={'finish_reason': 'stop'}))

    return ModelCatalog('offline-tools', {'offline-tools': ToolModel()})


def _worker(args):
    import uvicorn
    from naiyue_test_agent import application
    from scripts.web_probe_app import create_probe_app, command_headers, async_command_headers
    from naiyue_test_agent.web.settings import WebSettings

    root = Path(args.worker)
    workspace = root / 'workspace'
    workspace.mkdir(exist_ok=True)
    for path, text in [(workspace / 'sample.txt', 'Local fixture file.\n'),
                       (root / 'outside-first.txt', 'Approved external fixture content.\n'),
                       (root / 'outside-second.txt', 'Rejected external fixture content.\n')]:
        if not path.exists():
            path.write_text(text)
    assets = Path(args.frontend_dist) if args.frontend_dist else root / 'assets'
    if not args.frontend_dist:
        assets.mkdir(exist_ok=True)
        (assets / 'index.html').write_text('<html>offline probe</html>')
    application.load_model_catalog = lambda _: _catalog(root)
    logging.basicConfig(level=logging.WARNING)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        settings = WebSettings(config_path='unused', database_path=root / 'sessions.sqlite',
            frontend_dist=assets, workspace_root=workspace, port=port, access_token=ACCESS)
        descriptor = os.open(root / 'ready.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            json.dump({'port': port}, output)
        try:
            uvicorn.Server(uvicorn.Config(create_probe_app(settings), access_log=False, log_level='warning')).run(sockets=[listener])
        except KeyboardInterrupt:
            logging.getLogger(__name__).info('offline_tools_worker_shutdown')


def _parent(args):
    import httpx

    deadline = time.monotonic() + args.timeout_seconds
    with tempfile.TemporaryDirectory(prefix='naiyue-web-tools-') as directory:
        root = Path(directory)
        sid = None
        for phase in ('start', 'restore'):
            if phase == 'restore':
                (root / 'ready.json').unlink()
            process, output, port = _launch(root, deadline, worker_module='scripts.probe_web_tools')
            origin = f'http://127.0.0.1:{port}'
            try:
                with httpx.Client(event_hooks={'request': [command_headers]}, base_url=origin, headers={'Authorization': 'Bearer ' + ACCESS,
                    'Origin': origin}, timeout=10) as client:
                    def ready():
                        try:
                            return client.get('/api/v1/scopes/chats/models').status_code == 200
                        except httpx.ConnectError:
                            return False
                    _until(ready, deadline)
                    if sid is None:
                        response = client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'offline-tools'})
                        response.raise_for_status()
                        sid = response.json()['session_id']
                    else:
                        client.post(f'/api/v1/scopes/chats/sessions/{sid}/prepare').raise_for_status()
                        restored = client.get(f'/api/v1/scopes/chats/sessions/{sid}')
                        restored.raise_for_status()
                        assert {'external-one', 'external-two', 'listing'} == {
                            m['tool_call_id'] for m in restored.json()['messages'] if m['type'] == 'tool'}
                    response = client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json={
                        'request_id': str(uuid4()), 'text': 'Use fixture tools' if phase == 'start' else 'Verify restored history'})
                    response.raise_for_status()
                    run = response.json()['run_id']
                    stream = client.get(f'/api/v1/scopes/chats/runs/{run}/events')
                    stream.raise_for_status()
                    if phase == 'start':
                        assert 'approval_requested' in stream.text
                        value = client.get(f'/api/v1/scopes/chats/sessions/{sid}').json()
                        batch, = value['approval_batches']
                        assert value['pending_approval'] and len(batch['items']) == 2
                        assert [row['tool_call_id'] for row in batch['items']] == ['external-one', 'external-two']
                        body = {'request_id': str(uuid4()), 'origin_run_id': run, 'approval_batch_id': batch['approval_batch_id'],
                            'decisions': [{'approval_id': row['approval_id'], 'decision': decision}
                                for row, decision in zip(batch['items'], ['approve', 'reject'], strict=True)]}
                        resumed = client.post(f'/api/v1/scopes/chats/sessions/{sid}/approvals', json=body)
                        resumed.raise_for_status()
                        new_run = resumed.json()['run_id']
                        assert new_run != run
                        events = client.get(f'/api/v1/scopes/chats/runs/{new_run}/events')
                        events.raise_for_status()
                        assert 'tool_call_finished' in events.text
                        assert client.post(f'/api/v1/scopes/chats/sessions/{sid}/approvals', json=body).json() == resumed.json()
                        view = client.get(f'/api/v1/scopes/chats/runs/{new_run}').json()
                        assert view['status'] == 'completed' and not view['incomplete']
                        assert view['tool_states']['external-one']['outcome'] == 'success'
                        assert view['tool_states']['external-two']['outcome'] == 'not_executed'
                    else:
                        view = client.get(f'/api/v1/scopes/chats/runs/{run}').json()
                        assert view['status'] == 'completed' and view['messages'][-1]['content'] == 'TOOL HISTORY RESTORED'
            finally:
                _stop(process, output, deadline)
    print('web_tools_probe=PASS mode=offline tcp=True approval=correlated mixed_decisions=True '
          'resume_run=new duplicate_safe=True tool_results=paired two_processes=True history_used=True')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout-seconds', type=float, default=60)
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    parser.add_argument('--frontend-dist', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 10 <= args.timeout_seconds <= 120:
        parser.error('timeout-seconds must be between 10 and 120')
    try:
        _worker(args) if args.worker else _parent(args)
        return 0
    except Exception as error:
        print(f'web_tools_probe=FAIL error_type={type(error).__name__}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
