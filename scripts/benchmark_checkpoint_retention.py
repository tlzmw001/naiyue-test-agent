"""Compare real SQLite finish costs with synthetic models and disposable stores.

Run with python -m scripts.benchmark_checkpoint_retention --output <result.json>.
No provider, user settings or existing conversation database is accessed.
"""

import argparse
import asyncio
from collections import Counter
import json
import logging
from pathlib import Path
import platform
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph import END

from naiyue_test_agent import application
from naiyue_test_agent.application import checkpoint_retention
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.application.session_persistence import SessionPersistence
from naiyue_test_agent.application.session_storage import SessionStorage
from naiyue_test_agent.checkpoint_settings import CheckpointRetentionSettings
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings


class SyntheticModel(BaseChatModel):
    payload_chars: int
    calls: int = 0

    @property
    def _llm_type(self):
        return 'checkpoint-benchmark-synthetic'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='a' * self.payload_chars))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def distribution(values):
    ordered = sorted(values)
    return dict(n=len(ordered), median_ms=median(ordered),
                p95_ms=ordered[min(len(ordered) - 1, int(len(ordered) * .95))], max_ms=ordered[-1])


async def sample(directory, case, enabled, repetition):
    model = SyntheticModel(payload_chars=case['payload_chars'])
    limits = {'max_delete_bytes': case['max_delete_bytes']} if 'max_delete_bytes' in case else {}
    settings = AppSettings(checkpoint_retention=CheckpointRetentionSettings(
        enabled=enabled, trigger_checkpoints=case['threshold'], **limits))
    samples, active = {}, {}
    finish = SessionPersistence.afinish
    save = SessionStorage.finish_run
    prune = checkpoint_retention.prune_after_finish

    async def observe_finish(self, sid, rid, *args):
        active[sid] = rid
        start = perf_counter()
        try:
            return await finish(self, sid, rid, *args)
        finally:
            samples.setdefault(rid, {})['finish_ms'] = (perf_counter() - start) * 1000
            active.pop(sid, None)

    def observe_save(self, sid, *args, **kwargs):
        start = perf_counter()
        try:
            return save(self, sid, *args, **kwargs)
        finally:
            rid = active.get(sid)
            if rid:
                samples.setdefault(rid, {})['required_save_ms'] = (perf_counter() - start) * 1000

    async def observe_prune(agent, storage, sid, rid, *args, **kwargs):
        start = perf_counter()
        result = await prune(agent, storage, sid, rid, *args, **kwargs)
        samples.setdefault(rid, {}).update(prune_ms=(perf_counter() - start) * 1000, **result)
        return result

    with (patch.object(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model})),
          patch.object(SessionPersistence, 'afinish', observe_finish),
          patch.object(SessionStorage, 'finish_run', observe_save),
          patch.object(checkpoint_retention, 'prune_after_finish', observe_prune)):
        async with open_async_persistent_application('unused', database_path=directory / 'state.sqlite',
                tools=[], system_prompt='', response_format=None, settings=settings) as app:
            sessions = [await app.acreate_session('A') for _ in range(case['sessions'])]
            seeded = bool(case.get('backlog') or case.get('oversize_prefix'))
            if seeded:
                sid = sessions[0]
                await app.arun(sid, [('user', 'synthetic seed')])
                config = {'configurable': {'thread_id': app._storage.get(sid)['thread_id']}}
                for _ in range(case.get('backlog', 0)):
                    await app._agent.aupdate_state(config, None, as_node=END)
                if case.get('backlog'):
                    await app.aprepare_session(sid)
                if case.get('oversize_prefix'):
                    saved = await app._agent.checkpointer.aget_tuple(config)
                    def fixtures():
                        for number in range(case['oversize_prefix']):
                            identity = f'00000000-0000-0000-0000-{number:012d}'
                            checkpoint = {**saved.checkpoint, 'id': identity, 'channel_values': {
                                **saved.checkpoint['channel_values'], 'messages': [HumanMessage(
                                    id=identity, content='x' * 20000)]}}
                            kind, blob = app._agent.checkpointer.serde.dumps_typed(checkpoint)
                            yield config['configurable']['thread_id'], '', identity, None, kind, blob, b'{}'
                    with app._storage.connection:
                        app._storage.connection.executemany('INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?)', fixtures())
            if case.get('historical_boundaries'):
                historical = await app.acreate_session('A')
                thread = app._storage.get(historical)['thread_id']
                with app._storage.connection:
                    app._storage.connection.executemany('''INSERT INTO naiyue_reply_boundaries
                        (session_id,thread_id,reply_id,input_message_id,source_checkpoint_id,input_sequence,source_thread_id)
                        VALUES(?,?,?,?,?,?,?)''', ((historical, thread, f'reply-{i}', f'input-{i}', None, i, thread)
                        for i in range(case['historical_boundaries'])))
            samples.clear()
            async def conversation(sid):
                for _ in range(case['turns']):
                    start = perf_counter()
                    await app.arun(sid, [('user', 'q' * case['payload_chars'])])
                    elapsed = (perf_counter() - start) * 1000
                    rid = app._storage.get(sid)['run_id']
                    samples[rid]['task_ms'] = elapsed
                    thread = app._storage.get(sid)['thread_id']
                    samples[rid]['retained_checkpoints'] = app._storage.connection.execute(
                        'SELECT COUNT(*) FROM checkpoints WHERE thread_id=?', (thread,)).fetchone()[0]
            await asyncio.gather(*(conversation(sid) for sid in sessions))
            counts = app._storage._read('''SELECT COUNT(*) AS checkpoints,
                COALESCE(SUM(length(checkpoint)+length(metadata)),0) AS checkpoint_bytes FROM checkpoints''')[0]
            archive = app._storage._read('SELECT COUNT(*) AS messages FROM naiyue_chat_messages')[0]['messages']
            expected = 2 * (case['turns'] * case['sessions'] + seeded)
            if archive != expected:
                raise AssertionError(f'Archive count {archive} != {expected}')
    rows = list(samples.values())
    if len(rows) != case['turns'] * case['sessions']:
        raise AssertionError('Missing finish sample')
    if any('reason' not in row for row in rows):
        raise AssertionError('Retention failed instead of returning measured facts; inspect log')
    expected_calls = case['turns'] * case['sessions'] + seeded
    if model.calls != expected_calls:
        raise AssertionError('Unexpected synthetic model calls')
    return dict(case=case['name'], enabled=enabled, repetition=repetition, config=case,
                samples=rows, checkpoints=counts['checkpoints'], checkpoint_bytes=counts['checkpoint_bytes'],
                archive_messages=archive, model_calls=model.calls, provider_requests=0)


async def main(args):
    cases = [dict(name='below_threshold', sessions=1, turns=12, payload_chars=512, threshold=100000),
             dict(name='steady', sessions=1, turns=args.turns, payload_chars=4096, threshold=128),
             dict(name='backlog', sessions=1, turns=12, payload_chars=512, threshold=128, backlog=384),
             dict(name='concurrent', sessions=2, turns=max(24, args.turns // 2), payload_chars=1024, threshold=128)]
    cases.extend(dict(name=f'oversize_prefix_{count}', sessions=1, turns=4, payload_chars=32,
        threshold=2, oversize_prefix=count, max_delete_bytes=16000) for count in (128, 1280))
    cases.extend(dict(name=f'historical_boundaries_{count}', sessions=1, turns=12, payload_chars=32,
        threshold=2, historical_boundaries=count) for count in (1000, 10000))
    if args.cases:
        cases = [case for case in cases if case['name'] in args.cases]
    if args.max_delete_bytes is not None:
        for case in cases:
            case.setdefault('max_delete_bytes', args.max_delete_bytes)
    results = []
    with TemporaryDirectory(prefix='nyt-checkpoint-benchmark-') as temporary:
        root = Path(temporary)
        for case in cases:
            for repetition in range(args.repeats):
                for enabled in ([False, True] if repetition % 2 == 0 else [True, False]):
                    path = root / f'{case["name"]}-{repetition}-{enabled}'
                    path.mkdir()
                    result = await sample(path, case, enabled, repetition)
                    results.append(result)
                    print(json.dumps(dict(case=case['name'], enabled=enabled, repetition=repetition,
                        checkpoints=result['checkpoints'], deleted=sum(r['deleted_checkpoints'] for r in result['samples']))), flush=True)
    summary = []
    for case in cases:
        for enabled in (False, True):
            runs = [r for r in results if r['case'] == case['name'] and r['enabled'] == enabled]
            rows = [row for run in runs for row in run['samples']]
            deleting = [row['prune_ms'] for row in rows if row['reason'] == 'pruned']
            summary.append(dict(case=case['name'], enabled=enabled,
                **{phase: distribution([r[phase] for r in rows]) for phase in
                   ('task_ms', 'finish_ms', 'required_save_ms', 'prune_ms')},
                deleting=distribution(deleting) if deleting else None,
                reasons=dict(Counter(r['reason'] for r in rows)),
                final_checkpoints=[r['checkpoints'] for r in runs],
                final_checkpoint_bytes=[r['checkpoint_bytes'] for r in runs]))
    report = dict(platform=platform.platform(), python=platform.python_version(),
                  provider_requests=0, repeats=args.repeats, summary=summary, runs=results)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(output=str(args.output), summary=summary), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--turns', type=int, default=60)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--max-delete-bytes', type=int)
    parser.add_argument('--cases', nargs='+', choices=['below_threshold', 'steady', 'backlog', 'concurrent',
        'oversize_prefix_128', 'oversize_prefix_1280', 'historical_boundaries_1000', 'historical_boundaries_10000'])
    options = parser.parse_args()
    if options.turns < 24 or options.repeats < 1:
        parser.error('turns must be >=24 and repeats >=1')
    if options.max_delete_bytes is not None and options.max_delete_bytes <= 0:
        parser.error('max-delete-bytes must be positive')
    options.output.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=options.output.with_suffix('.log'), level=logging.WARNING)
    asyncio.run(main(options))
