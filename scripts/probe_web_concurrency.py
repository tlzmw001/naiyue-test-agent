"""Bounded offline multi-session TCP/SSE, approvals and two-process archive probe."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import socket
import tempfile
import time
from uuid import uuid4

from .probe_web import ACCESS, _launch, _stop, _until


def _record(root, entry):
    # Synthetic labels and operation counts only; never capture request bodies.
    with (root / 'ledger.jsonl').open('a', encoding='utf-8') as file:
        file.write(json.dumps(entry) + '\n')


def _catalog(root):
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGenerationChunk
    from naiyue_test_agent.model_access import ModelCatalog

    class ConcurrentModel(BaseChatModel):
        @property
        def _llm_type(self):
            return 'offline-web-concurrency'

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, *args, **kwargs):
            raise AssertionError('Synchronous model invocation is forbidden')

        async def _astream(self, messages, **kwargs):
            humans = [m.content for m in messages if isinstance(m, HumanMessage)]
            first = humans[0]
            assert first in ('OFFLINE_A', 'OFFLINE_B', 'child-B')
            _record(root, {'kind': 'model', 'label': first})
            results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
            identity, tool_call = uuid4().hex, None
            if len(humans) > 1:
                assert first == 'OFFLINE_B' and {'write-b', 'delegate-b'} == results.keys()
                text = 'RESTORED_B'
            elif first == 'OFFLINE_A':
                yield ChatGenerationChunk(message=AIMessageChunk(content='A_DELTA', id=identity))
                await asyncio.Event().wait()
                raise AssertionError('A can only end by cancellation')
            elif first == 'OFFLINE_B' and not results:
                yield ChatGenerationChunk(message=AIMessageChunk(content='B_DELTA', id=identity))
                await asyncio.to_thread(_until, lambda: (root / 'release-b').exists(), time.monotonic() + 60)
                tool_call = ('write', {'path': str(root / 'outside.txt'), 'content': 'COMMITTED_ONCE'}, 'write-b')
                text = ''
            elif first == 'OFFLINE_B' and 'delegate-b' not in results:
                assert results['write-b'].status == 'success'
                tool_call = ('subagent', {'agent': 'tester', 'task': 'child-B'}, 'delegate-b')
                text = ''
            elif first == 'child-B' and not results:
                tool_call = ('read', {'path': str(root / 'outside.txt')}, 'read-child')
                text = ''
            elif first == 'child-B':
                assert results['read-child'].content == 'COMMITTED_ONCE'
                text = 'CHILD_REPORT'
            else:
                assert json.loads(results['delegate-b'].content)['report'] == 'CHILD_REPORT'
                text = 'B_COMPLETE'
            yield ChatGenerationChunk(message=AIMessageChunk(content=text, id=identity))
            if tool_call:
                name, args, call_id = tool_call
                yield ChatGenerationChunk(message=AIMessageChunk(content='', id=identity, tool_call_chunks=[{
                    'name': name, 'args': json.dumps(args), 'id': call_id, 'index': 0}],
                    response_metadata={'finish_reason': 'tool_calls'}))
            else:
                yield ChatGenerationChunk(message=AIMessageChunk(content='', id=identity,
                    response_metadata={'finish_reason': 'stop'}))

    return ModelCatalog('offline-concurrent', {'offline-concurrent': ConcurrentModel()})


def _worker(args):
    import uvicorn
    from naiyue_test_agent import application
    from naiyue_test_agent.tools import SubAgentSpec, write as write_module
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.web.app import create_web_app
    from naiyue_test_agent.web.settings import WebSettings

    root = Path(args.worker)
    workspace, assets = root / 'workspace', root / 'assets'
    workspace.mkdir(exist_ok=True)
    assets.mkdir(exist_ok=True)
    (assets / 'index.html').write_text('<html>offline concurrency probe</html>')
    application.load_model_catalog = lambda _: _catalog(root)
    replace = write_module.os.replace
    def record_commit(source, target):
        result = replace(source, target)
        if Path(target) == (root / 'outside.txt').resolve():
            _record(root, {'kind': 'commit'})
        return result
    write_module.os.replace = record_commit
    logging.basicConfig(level=logging.WARNING)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        settings = WebSettings(config_path='unused', database_path=root / 'sessions.sqlite', frontend_dist=assets,
            workspace_root=workspace, port=port, access_token=ACCESS, runtime_settings=AppSettings(max_active_runs=2,
                subagents=(SubAgentSpec(name='tester', description='Read approved fixture', system_prompt='Read fixture',
                                       tool_names=('read',)),)))
        app = create_web_app(settings)
        lifespan = app.router.lifespan_context
        @asynccontextmanager
        async def observed(server):
            async with lifespan(server):
                def event(value):
                    if value.event_type == 'tool_call_finished':
                        _record(root, {'kind': 'tool', 'name': value.data.tool_name, 'outcome': value.data.outcome})
                unsubscribe = server.state.service.application.observability.subscribe(event)
                try:
                    yield
                finally:
                    unsubscribe()
        app.router.lifespan_context = observed
        descriptor = os.open(root / 'ready.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            json.dump({'port': port}, output)
        try:
            uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='warning')).run(sockets=[listener])
        except KeyboardInterrupt:
            logging.getLogger(__name__).info('offline_concurrency_worker_shutdown')


def _approve(client, sid, batch):
    body = {'request_id': str(uuid4()), 'origin_run_id': batch['origin_run_id'],
            'approval_batch_id': batch['approval_batch_id'], 'decisions': [
                {'approval_id': item['approval_id'], 'decision': 'approve'} for item in batch['items']]}
    response = client.post(f'/api/sessions/{sid}/approvals', json=body)
    response.raise_for_status()
    assert client.post(f'/api/sessions/{sid}/approvals', json=body).json() == response.json()
    return response.json()


def _parent(args):
    import httpx

    deadline = time.monotonic() + args.timeout_seconds
    with tempfile.TemporaryDirectory(prefix='naiyue-web-concurrency-') as directory:
        root, ids = Path(directory), []
        for phase in ('start', 'restore'):
            if phase == 'restore':
                (root / 'ready.json').unlink()
            process, output, port = _launch(root, deadline, worker_module='scripts.probe_web_concurrency')
            origin = f'http://127.0.0.1:{port}'
            try:
                with httpx.Client(base_url=origin, timeout=10,
                    headers={'Authorization': 'Bearer ' + ACCESS, 'Origin': origin}) as client:
                    def ready():
                        try:
                            return client.get('/api/models').status_code == 200
                        except httpx.ConnectError:
                            return False
                    _until(ready, deadline)
                    if phase == 'restore':
                        before = (root / 'ledger.jsonl').read_text()
                        for sid in ids:
                            prepared = client.post(f'/api/sessions/{sid}/prepare')
                            prepared.raise_for_status()
                            restored = client.get(f'/api/sessions/{sid}').json()
                            assert restored['can_run'] and not restored['busy'] and 'recovery' not in restored
                        children = client.get(f'/api/sessions/{ids[1]}/subagents').json()
                        child, = children
                        assert child['status'] == 'completed'
                        detail = client.get(f'/api/sessions/{ids[1]}/subagents/{child["agent_id"]}').json()
                        assert any(m['type'] == 'tool' and m['content'] == 'COMMITTED_ONCE' for m in detail['messages'])
                        assert client.get(f'/api/sessions/{ids[0]}/subagents').json() == []
                        assert (root / 'ledger.jsonl').read_text() == before
                        response = client.post(f'/api/sessions/{ids[1]}/runs', json={
                            'request_id': str(uuid4()), 'text': 'Verify saved context'})
                        response.raise_for_status()
                        rid = response.json()['run_id']
                        client.get(f'/api/runs/{rid}/events').raise_for_status()
                        final = client.get(f'/api/runs/{rid}').json()
                        assert final['status'] == 'completed' and final['messages'][-1]['content'] == 'RESTORED_B'
                        continue
                    for _ in range(2):
                        response = client.post('/api/sessions', json={'model_id': 'offline-concurrent'})
                        response.raise_for_status()
                        ids.append(response.json()['session_id'])
                    runs = []
                    for sid, prompt in zip(ids, ('OFFLINE_A', 'OFFLINE_B'), strict=True):
                        body = {'request_id': str(uuid4()), 'text': prompt}
                        response = client.post(f'/api/sessions/{sid}/runs', json=body)
                        response.raise_for_status()
                        assert client.post(f'/api/sessions/{sid}/runs', json=body).json() == response.json()
                        runs.append(response.json()['run_id'])
                    # Both producers are live before either stream detaches.
                    for sid, rid, delta in zip(ids, runs, ('A_DELTA', 'B_DELTA'), strict=True):
                        with client.stream('GET', f'/api/runs/{rid}/events') as stream:
                            stream.raise_for_status()
                            for line in stream.iter_lines():
                                if line.startswith('data: ') and '"event_type"' in line:
                                    event = json.loads(line[6:])
                                    assert event['session_id'] == sid
                                    if event['event_type'] == 'message_delta':
                                        assert delta in line
                                        break
                            else:
                                raise AssertionError('Missing live delta')
                    assert all(not client.get(f'/api/runs/{rid}').json()['done'] for rid in runs)
                    for kind in ('steering', 'follow_up'):
                        response = client.post(f'/api/runs/{runs[0]}/controls', json={
                            'request_id': str(uuid4()), 'kind': kind, 'text': 'A ' + kind})
                        response.raise_for_status()
                    stop_body = {'request_id': str(uuid4()), 'kind': 'stop'}
                    stopped = client.post(f'/api/runs/{runs[0]}/controls', json=stop_body)
                    stopped.raise_for_status()
                    assert stopped.json()['retained'] == ['A steering', 'A follow_up']
                    assert client.post(f'/api/runs/{runs[0]}/controls', json=stop_body).json() == stopped.json()
                    _until(lambda: client.get(f'/api/runs/{runs[0]}').json()['done'], deadline)
                    assert client.get(f'/api/runs/{runs[0]}').json()['status'] == 'stopped'
                    assert not client.get(f'/api/runs/{runs[1]}').json()['done']
                    (root / 'release-b').touch()
                    client.get(f'/api/runs/{runs[1]}/events').raise_for_status()
                    batch, = client.get(f'/api/sessions/{ids[1]}').json()['approval_batches']
                    assert batch['kind'] == 'paused_root' and not (root / 'outside.txt').exists()
                    resumed = _approve(client, ids[1], batch)
                    resumed_id = resumed['run_id']
                    assert resumed_id != runs[1]
                    def child_pending():
                        batches = client.get(f'/api/sessions/{ids[1]}').json()['approval_batches']
                        return batches and batches[0]['kind'] == 'live_child' and batches[0]
                    child_batch = _until(child_pending, deadline)
                    assert child_batch['origin_run_id'] == resumed_id
                    assert _approve(client, ids[1], child_batch)['run_id'] == resumed_id
                    client.get(f'/api/runs/{resumed_id}/events').raise_for_status()
                    final = client.get(f'/api/runs/{resumed_id}').json()
                    assert final['status'] == 'completed' and final['messages'][-1]['content'] == 'B_COMPLETE'
                    assert (root / 'outside.txt').read_text() == 'COMMITTED_ONCE'
            finally:
                # A failed probe must also let a worker thread settle on shutdown.
                (root / 'release-b').touch(exist_ok=True)
                _stop(process, output, deadline)
        ledger = [json.loads(line) for line in (root / 'ledger.jsonl').read_text().splitlines()]
        assert sum(row['kind'] == 'model' for row in ledger) == 7
        assert sum(row['kind'] == 'commit' for row in ledger) == 1
        assert [(row['name'], row['outcome']) for row in ledger if row['kind'] == 'tool'] == [
            ('write', 'success'), ('read', 'success'), ('subagent', 'success')]
    print('web_concurrency_probe=PASS tcp=True simultaneous_delta=True disconnect_keeps_both=True '
          'controls=isolated approvals=root_child duplicate_safe=True two_processes=True '
          'archive_read_replay=False model_calls=7 write_commits=1 read_calls=1 provider_requests=0')


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
        print(f'web_concurrency_probe=FAIL error_type={type(error).__name__}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
