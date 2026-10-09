"""Bounded offline TCP/SSE and two-process Web persistence probe; no model credentials."""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4


ACCESS = 'offline-probe-access'


def _catalog():
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessageChunk, HumanMessage
    from langchain_core.outputs import ChatGenerationChunk
    from naiyue_test_agent.model_access import ModelCatalog

    class OfflineModel(BaseChatModel):
        @property
        def _llm_type(self):
            return 'offline-web-stream'

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, *args, **kwargs):
            raise AssertionError('Synchronous model invocation is forbidden')

        async def _astream(self, messages, **kwargs):
            humans = [m.content for m in messages if isinstance(m, HumanMessage)]
            identity = uuid4().hex
            if humans[-1] == 'BLOCK':
                yield ChatGenerationChunk(message=AIMessageChunk(content='FIRST-中文', id=identity))
                await asyncio.Event().wait()
            else:
                assert humans == ['BLOCK', 'resume'], 'Persisted history was not used'
                yield ChatGenerationChunk(message=AIMessageChunk(content='RESTORED', id=identity))
                yield ChatGenerationChunk(message=AIMessageChunk(content='', id=identity,
                    response_metadata={'finish_reason': 'stop'}))

    return ModelCatalog('offline', {'offline': OfflineModel()})


def _worker(args):
    import uvicorn
    from naiyue_test_agent import application
    from naiyue_test_agent.web.app import create_web_app
    from naiyue_test_agent.web.settings import WebSettings

    application.load_model_catalog = lambda _: _catalog()
    logging.basicConfig(level=logging.WARNING)
    root = Path(args.worker)
    assets = root / 'assets'
    assets.mkdir(exist_ok=True)
    (assets / 'index.html').write_text('<html>offline probe</html>')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        settings = WebSettings(config_path='unused', database_path=root / 'sessions.sqlite',
            frontend_dist=assets, port=port, access_token=ACCESS)
        app = create_web_app(settings)
        descriptor = os.open(root / 'ready.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            json.dump({'port': port}, output)
        try:
            uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='warning')).run(sockets=[listener])
        except KeyboardInterrupt:
            logger = logging.getLogger(__name__)
            logger.info('offline_worker_shutdown_signal')


def _until(check, deadline):
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.03)
    raise TimeoutError('Probe deadline exceeded')


def _launch(root, deadline, *, worker_module='scripts.probe_web'):
    env = {**os.environ, 'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'}
    output = tempfile.TemporaryFile()
    process = subprocess.Popen([sys.executable, '-m', worker_module, '--worker', str(root)],
                               env=env, stdout=output, stderr=output)
    try:
        def ready():
            if process.poll() is not None:
                raise RuntimeError('Offline Web worker failed')
            try:
                return json.loads((root / 'ready.json').read_text())['port']
            except (FileNotFoundError, json.JSONDecodeError):
                return None
        port = _until(ready, deadline)
        return process, output, port
    except BaseException:
        process.terminate()
        process.wait(timeout=5)
        output.close()
        raise


def _stop(process, output, deadline):
    try:
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=max(1, deadline - time.monotonic())) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        output.close()


def _parent(args):
    import httpx

    deadline = time.monotonic() + args.timeout_seconds
    with tempfile.TemporaryDirectory(prefix='naiyue-web-offline-') as directory:
        root = Path(directory)
        session = None
        for phase in ('start', 'restore'):
            if phase == 'restore':
                (root / 'ready.json').unlink()  # Only this probe's private readiness marker.
            process, output, port = _launch(root, deadline)
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
                    if session is None:
                        created = client.post('/api/sessions', json={'model_id': 'offline'})
                        created.raise_for_status()
                        session = created.json()['session_id']
                    else:
                        restored = client.get('/api/sessions/' + session)
                        restored.raise_for_status()
                        assert [m['content'] for m in restored.json()['messages']] == ['BLOCK']
                    body = {'request_id': str(uuid4()), 'text': 'BLOCK' if phase == 'start' else 'resume'}
                    response = client.post(f'/api/sessions/{session}/runs', json=body)
                    response.raise_for_status()
                    run = response.json()['run_id']
                    with client.stream('GET', f'/api/runs/{run}/events') as stream:
                        stream.raise_for_status()
                        observed = False
                        for line in stream.iter_lines():
                            if time.monotonic() > deadline:
                                raise TimeoutError('Probe deadline exceeded')
                            if line.startswith('data:'):
                                event = json.loads(line[5:])
                                if event['event_type'] == 'message_delta':
                                    observed = True
                                    break
                        assert observed
                    # Closing an HTTP consumer must not stop its separately owned producer.
                    if phase == 'start':
                        assert client.get(f'/api/runs/{run}').json()['status'] == 'running'
                        for kind, text in [('steering', 'retained-steer'), ('follow_up', 'retained-follow')]:
                            receipt = client.post(f'/api/runs/{run}/controls', json={
                                'request_id': str(uuid4()), 'kind': kind, 'text': text})
                            receipt.raise_for_status()
                        receipt = client.post(f'/api/runs/{run}/controls', json={'request_id': str(uuid4()), 'kind': 'stop'})
                        receipt.raise_for_status()
                        assert receipt.json()['retained'] == ['retained-steer', 'retained-follow']
                    def finished():
                        value = client.get(f'/api/runs/{run}').json()
                        return value if value['done'] else None
                    view = _until(finished, deadline)
                    assert view['status'] == ('stopped' if phase == 'start' else 'completed')
                    if phase == 'start':
                        assert view['retained'] == ['retained-steer', 'retained-follow']
                        assert all(m['provisional'] for m in view['messages'] if m['type'] == 'ai')
                    else:
                        assert view['messages'][-1]['content'] == 'RESTORED'
                    assert client.post(f'/api/sessions/{session}/runs', json=body).json() == response.json()
            finally:
                _stop(process, output, deadline)
    print('web_probe=PASS mode=offline tcp=True sse_delta=True disconnect_keeps_run=True '
          'stop=True retained=True two_processes=True history_used=True duplicate_safe=True')


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
        print(f'web_probe=FAIL error_type={type(error).__name__}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
