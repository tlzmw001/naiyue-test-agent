"""Disposable offline graph/process/HTTP measurements; no user config or providers."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import math
import os
from pathlib import Path
import platform
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessRequest, ProcessOutcome
from naiyue_test_agent.tools.settings import BashSettings
from naiyue_test_agent.web.app import create_web_app
from naiyue_test_agent.web.settings import WebSettings
from scripts.release_background_smoke import process_alive, wait_task


class FixedModel(BaseChatModel):
    calls: int = 0

    @property
    def _llm_type(self):
        return 'offline-background-benchmark'

    def bind_tools(self, *args, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        raise AssertionError('Synchronous benchmark model request')

    async def _astream(self, messages, **kwargs):
        self.calls += 1
        yield ChatGenerationChunk(message=AIMessageChunk(content='fixed reply', response_metadata={'finish_reason': 'stop'}))


def distribution(values):
    if not values:
        return {'n': 0, 'p50_ms': None, 'p95_ms': None, 'max_ms': None}
    ordered = sorted(values)
    return dict(n=len(values), p50_ms=median(ordered),
                p95_ms=ordered[math.ceil(len(ordered) * .95) - 1], max_ms=ordered[-1])


def process_request(root, script):
    return ProcessRequest(argv=[sys.executable, '-u', '-c', script], cwd=root, env=dict(os.environ),
        timeout_seconds=None, merge_output=True, execution_environment={'kind': 'host'})


async def load(app, root, sid, script, *, call='load', limit=67108864, runner=None):
    return await app._sessions.background_tasks.submit(session_id=sid, workspace_root=root,
        archive_root=app._sessions.session_archive_root(sid),
        run_id='benchmark', tool_call_id=call, agent_path='root', command='benchmark disposable load', name='bash',
        request=process_request(root, script), settings=BashSettings(max_archive_bytes=limit),
        runner=runner or HostProcessRunner(), control=None)


@asynccontextmanager
async def environment(root):
    root.mkdir(exist_ok=True)
    assets = root / 'assets'
    assets.mkdir()
    (assets / 'index.html').write_text('<html>benchmark fixture</html>')
    model = FixedModel()
    settings = AppSettings()
    web_settings = WebSettings(config_path=root / 'unused', database_path=root / 'sessions.sqlite',
        frontend_dist=assets, workspace_root=root, runtime_settings=settings)
    def factory():
        return open_async_persistent_application('unused', database_path=web_settings.database_path,
            tools=[], system_prompt='', response_format=None, settings=settings)
    with patch.object(application, 'load_model_catalog', lambda _: ModelCatalog('offline', {'offline': model})):
        web = create_web_app(web_settings, application_factory=factory)
        async with web.router.lifespan_context(web):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url=web_settings.origin,
                headers={'Authorization': 'Bearer ' + web_settings.access_token, 'Origin': web_settings.origin}) as client:
                yield web.state.service.application, client, model


async def sample(root, case, options):
    root = root.resolve()
    turns, metadata, output, stop_times = [], [], [], []
    stop = asyncio.Event()
    pid, identity, cleanup, saved_bytes, observed, close_ms = None, None, True, 0, 0, 0
    interval = options.poll_seconds if case == 'management_stress' else 3
    async with environment(root) as (app, client, model):
        sid = await app.acreate_workspace_session('offline', root)
        if case != 'baseline':
            script = "import os,threading,time;print(os.getpid(),flush=True)\n"
            if case in ('output', 'output_open', 'management_stress'):
                script += f"while not os.path.exists({str(root / 'write-ready')!r}): time.sleep(.01)\n"
                script += f"while True:\n os.write(1,b'x'*4096)\n time.sleep({4096 / options.output_bytes_per_second!r})\n"
            else:
                script += 'threading.Event().wait()'
            identity = (await load(app, root, sid, script, limit=options.archive_bytes))['task_id']
            ready = await wait_task(app, identity, lambda row: bool(row['output']))
            pid = int(ready['output'].splitlines()[0])
            (root / 'write-ready').touch()
        for _ in range(options.warmup):
            await app.arun(sid, [('user', 'warmup')])
        async def monitor():
            while not stop.is_set():
                start = perf_counter()
                response = await client.get('/api/background-tasks')
                assert response.status_code == 200, response.text
                metadata.append((perf_counter() - start) * 1000)
                if identity and case in ('output_open', 'management_stress'):
                    start = perf_counter()
                    response = await client.get(f'/api/background-tasks/{identity}/output')
                    assert response.status_code == 200, response.text
                    output.append((perf_counter() - start) * 1000)
                try:
                    await asyncio.wait_for(stop.wait(), interval)
                except TimeoutError:
                    continue
        monitor_task = asyncio.create_task(monitor())
        try:
            for _ in range(options.turns):
                start = perf_counter()
                result = await app.arun(sid, [('user', 'fixed task')])
                assert result['messages'][-1].content == 'fixed reply'
                turns.append((perf_counter() - start) * 1000)
                await asyncio.sleep(options.turn_gap_seconds)
            if identity:
                before = await app.aget_background_task_output(identity)
                assert before['status'] == 'running', 'Output load ended before the measurement completed'
                start = perf_counter()
                ended = await app.astop_background_task(identity)
                stop_times.append((perf_counter() - start) * 1000)
                view = await app.aget_background_task_output(identity)
                cleanup, saved_bytes, observed = ended['cleanup_confirmed'], view['archive_saved_bytes'], view['observed_raw_bytes']
                assert cleanup and saved_bytes <= options.archive_bytes
            start = perf_counter()
        finally:
            stop.set()
            await monitor_task  # A failed verifier must fail the benchmark.
    close_ms = (perf_counter() - start) * 1000
    assert pid is None or not process_alive(pid), 'Benchmark load process survived close'
    return dict(case=case, turn_ms=turns, metadata_ms=metadata, output_ms=output, stop_ms=stop_times,
        close_ms=close_ms, cleanup_confirmed=cleanup, model_calls=model.calls, provider_requests=0,
        archive_saved_bytes=saved_bytes, observed_raw_bytes=observed, poll_seconds=interval)


async def history_and_flood(root, counts, repeats, archive_bytes):
    root = root.resolve()
    samples = []
    class FinishedRunner:
        async def run(self, request, consume, control):
            request.on_started()
            return ProcessOutcome(0, 'exited', True, True, True, True, 0)
    async with environment(root) as (app, client, _model):
        sid = await app.acreate_workspace_session('offline', root)
        current = 0
        for count in sorted(counts):
            for i in range(current, count):
                row = await load(app, root, sid, '', call=str(i), runner=FinishedRunner())
                await app._sessions.background_tasks._entries[row['task_id']].worker
            current = count
            values = []
            for _ in range(repeats):
                start = perf_counter()
                response = await client.get('/api/background-tasks')
                assert response.status_code == 200 and len(response.json()['items']) == min(20, count)
                values.append((perf_counter() - start) * 1000)
            samples.append(dict(records=count, metadata=distribution(values), fixture='synthetic terminal registry, no process load'))
        flood = await load(app, root, sid, "import os\nwhile True: os.write(1,b'x'*65536)", call='flood', limit=archive_bytes)
        start = perf_counter()
        ended = await wait_task(app, flood['task_id'], lambda row: row['status'] == 'finished')
        assert ended['termination'] == 'output_limit' and ended['cleanup_confirmed']
        assert ended['archive_saved_bytes'] <= archive_bytes
        return dict(history=samples, flood=dict(elapsed_ms=(perf_counter() - start) * 1000,
            archive_limit=archive_bytes, saved_bytes=ended['archive_saved_bytes'], cleanup_confirmed=True))


async def active_close(root):
    root = root.resolve()
    async with environment(root) as (app, _client, _model):
        sid = await app.acreate_workspace_session('offline', root)
        row = await load(app, root, sid, 'import os,threading;print(os.getpid(),flush=True);threading.Event().wait()')
        ready = await wait_task(app, row['task_id'], lambda value: bool(value['output']))
        pid = int(ready['output'])
        start = perf_counter()
    elapsed = (perf_counter() - start) * 1000
    assert not process_alive(pid), 'Benchmark active process survived application close'
    settled = await app._sessions.background_tasks.output(row['task_id'])
    assert settled['termination'] == 'app_close' and settled['cleanup_confirmed']
    return dict(close_ms=elapsed, cleanup_confirmed=True, termination=settled['termination'])


async def main(options):
    rows = []
    with TemporaryDirectory(prefix='nyt-background-benchmark-') as directory:
        root = Path(directory)
        for repetition in range(options.repeats):
            for case in options.cases:
                rows.append(await sample(root / f'{case}-{repetition}', case, options))
        extra = await history_and_flood(root / 'history', options.history_counts, options.turns, options.archive_bytes)
        closes = [await active_close(root / f'close-{i}') for i in range(options.repeats)]
    summary = {}
    for case in options.cases:
        selected = [row for row in rows if row['case'] == case]
        summary[case] = {field: distribution([value for row in selected for value in row[field]])
            for field in ('turn_ms', 'metadata_ms', 'output_ms', 'stop_ms')}
        summary[case]['close_ms'] = distribution([row['close_ms'] for row in selected])
    report = dict(environment=dict(platform=platform.platform(), python=sys.version.split()[0], provider_requests=0),
        options={k: v for k, v in vars(options).items() if k != 'output'}, summary=summary, samples=rows,
        active_close=distribution([row['close_ms'] for row in closes]), **extra)
    options.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(output=str(options.output), summary=summary, **extra), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--turns', type=int, default=40)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--warmup', type=int, default=2)
    parser.add_argument('--output-bytes-per-second', type=int, default=65536)
    parser.add_argument('--archive-bytes', type=int, default=67108864)
    parser.add_argument('--poll-seconds', type=float, default=.05)
    parser.add_argument('--turn-gap-seconds', type=float, default=.05)
    parser.add_argument('--history-counts', type=int, nargs='+', default=[0, 100, 1000])
    parser.add_argument('--cases', nargs='+', choices=['baseline', 'quiet', 'output', 'output_open', 'management_stress'],
        default=['baseline', 'quiet', 'output', 'output_open', 'management_stress'])
    args = parser.parse_args()
    if min(args.turns, args.repeats, args.output_bytes_per_second, args.archive_bytes) <= 0 or args.warmup < 0 or min(args.history_counts) < 0 or args.poll_seconds <= 0 or args.turn_gap_seconds < 0:
        parser.error('Counts/rates/limits/poll interval must be positive; warmup/history/gap must be nonnegative')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=args.output.with_suffix('.log'), level=logging.WARNING)
    asyncio.run(main(args))
