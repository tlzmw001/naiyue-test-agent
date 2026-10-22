"""Disposable fixed-model continuation and SQLite metadata measurements."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
import json
import logging
from pathlib import Path
import platform
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from pydantic import Field

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.application.subagent_records import role_fingerprint
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import SubAgentSpec, create_builtin_tools
from scripts.benchmark_background_tasks import distribution
from scripts.release_subagent_smoke import call, tool_result


class FixedModel(BaseChatModel):
    replies: list = Field(default_factory=list)
    calls: int = 0
    gate: Any = None

    @property
    def _llm_type(self):
        return 'offline-subagent-benchmark'

    def bind_tools(self, *args, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        raise AssertionError('Unexpected synchronous benchmark request')

    async def _astream(self, messages, **kwargs):
        self.calls += 1
        if self.gate is not None:
            entered, release = self.gate
            entered.set()
            await release.wait()
        reply = self.replies.pop(0)
        yield ChatGenerationChunk(message=AIMessageChunk(content=reply.content, tool_calls=reply.tool_calls,
            id=uuid4().hex, response_metadata={'finish_reason': 'tool_calls' if reply.tool_calls else 'stop'}))


@asynccontextmanager
async def environment(root):
    root.mkdir()
    parent, child = FixedModel(), FixedModel()
    role = SubAgentSpec('worker', 'Return a result', 'Return the requested result.', (), model_id='child', max_model_calls=1)
    settings = AppSettings(subagents=(role,))
    tools = [spec for spec in create_builtin_tools(settings.tools) if spec.tool.name in ('subagent', 'subagent_list')]
    with patch.object(application, 'load_model_catalog', lambda _: ModelCatalog('root', {'root': parent, 'child': child})):
        async with open_async_persistent_application('unused', database_path=root / 'state.sqlite',
                tools=tools, initial_active_tools=['subagent'], system_prompt='', response_format=None, settings=settings) as app:
            sid = await app.acreate_workspace_session('root', root)
            yield app, sid, parent, child, role


async def graph_samples(root, case, turns, warmup):
    values, identity = [], None
    async with environment(root) as (app, sid, parent, child, _role):
        for index in range(warmup + turns):
            args = dict(agent='worker', task=f'Return result-{index}')
            if case == 'continue' and identity is not None:
                args['agent_id'] = identity
            parent.replies.extend([call('subagent', args, f'delegate-{index}'), AIMessage(content='done')])
            child.replies.append(AIMessage(content=f'result-{index}'))
            start = perf_counter()
            result = await app.arun(sid, [('user', args['task'])])
            elapsed = (perf_counter() - start) * 1000
            feedback = tool_result(result, 'subagent')
            assert feedback.status == 'success', feedback.content
            envelope = json.loads(feedback.content)
            assert envelope['report'] == f'result-{index}'
            if case == 'continue' and identity is not None:
                assert envelope['agent_id'] == identity
            if case == 'fresh' and identity is not None:
                assert envelope['agent_id'] != identity
            identity = envelope['agent_id']
            if index >= warmup:
                values.append(elapsed)
        return dict(samples_ms=values, summary=distribution(values), root_model_calls=parent.calls,
            child_model_calls=child.calls, warmup=warmup, child_calls_per_delegation=1)


async def metadata_samples(root, counts, repeats):
    values = []
    async with environment(root) as (app, sid, parent, child, role):
        entered, release = asyncio.Event(), asyncio.Event()
        parent.gate = entered, release
        parent.replies.append(AIMessage(content='finished after metadata measurement'))
        task = asyncio.create_task(app.arun(sid, [('user', 'Hold while metadata is measured.')]))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            owner = app._sessions.require(sid)
            context = dict(session_id=sid, run_id=owner._run.run_id, agent_path='root', workspace_root=root)
            current = 0
            for count in sorted(set(counts)):
                for index in range(current, count):
                    identity = sha256(f'metadata-fixture-{index}'.encode()).hexdigest()
                    stamp = f'2026-01-01T00:00:00.{index:06d}+00:00'
                    app._storage.save_subagent(dict(session_id=sid, agent_id=identity,
                        agent_path='root/worker/' + identity, child_thread_id='subagent:' + identity,
                        agent='worker', role_fingerprint=role_fingerprint(role), model_id='child',
                        last_task='Synthetic failed-instance metadata; no executable checkpoint.', state='failed',
                        version=1, checkpoint_ns='', checkpoint_id=None, last_delegation_id=identity,
                        last_parent_run_id=owner._run.run_id, last_parent_batch_id='metadata-fixture',
                        last_parent_tool_call_id=identity, last_parent_reply_id=owner._run.reply_id,
                        created_at=stamp, updated_at=stamp), None)
                current = count
                calls = parent.calls + child.calls
                samples = []
                for _ in range(repeats):
                    start = perf_counter()
                    result = await owner.alist_subagents(parent_context=context, parent_state={})
                    samples.append((perf_counter() - start) * 1000)
                    page = json.loads(result)
                    assert len(page['items']) == min(20, count) and len(result.encode()) <= 8192
                    assert page['truncated'] is (count > 20)
                values.append(dict(synthetic_records=count, samples_ms=samples, summary=distribution(samples),
                    model_calls=parent.calls + child.calls - calls, fixture='failed registry rows, no child checkpoint'))
                assert values[-1]['model_calls'] == 0
        finally:
            release.set()
            await task
    return values


async def measure(root, *, turns, warmup, registry_counts, repeats):
    root = root.resolve(strict=True)
    assert turns > 0 and warmup > 0 and repeats > 0 and registry_counts and min(registry_counts) >= 0
    result = {}
    for case in ('fresh', 'continue'):
        result[case] = await graph_samples(root / case, case, turns, warmup)
    result['metadata'] = await metadata_samples(root / 'metadata', registry_counts, repeats)
    result['provider_requests'] = 0
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--turns', type=int, default=120)
    parser.add_argument('--warmup', type=int, default=3)
    parser.add_argument('--repeats', type=int, default=120)
    parser.add_argument('--registry-counts', type=int, nargs='+', default=[0, 100, 1000])
    options = parser.parse_args()
    if min(options.turns, options.warmup, options.repeats) <= 0 or min(options.registry_counts) < 0:
        parser.error('Sample/warmup counts must be positive; registry counts must be nonnegative')
    options.output.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=options.output.with_suffix('.log'), level=logging.WARNING)
    with TemporaryDirectory(prefix='nyt-subagent-benchmark-') as directory:
        result = asyncio.run(measure(Path(directory), turns=options.turns, warmup=options.warmup,
            registry_counts=options.registry_counts, repeats=options.repeats))
    result['environment'] = dict(platform=platform.platform(), python=sys.version.split()[0])
    options.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({case: result[case]['summary'] for case in ('fresh', 'continue')}, indent=2))


if __name__ == '__main__':
    main()
