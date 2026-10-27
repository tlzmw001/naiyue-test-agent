"""One optional deadline covers search discovery, groups and record checks."""

from dataclasses import replace
from runtime_context_helpers import ExecutionRoot, RunIdentity, ToolAuthorization, typed_context
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException

from naiyue_test_agent.tools import grep, find, search_protocol
from naiyue_test_agent.tools.paths import prepare_file_subject
from naiyue_test_agent.tools.permissions import execution_environment
from naiyue_test_agent.tools.permission_rules import PermissionSettings
from naiyue_test_agent.tools.processes import ProcessOutcome
from naiyue_test_agent.tools.settings import GrepSettings, FindSettings


def runtime(root, name):
    operation = 'list' if name == 'find' else 'search'
    subject = prepare_file_subject('.', root, operation)
    subject.update(tool_name=name, args={'path': '.', 'pattern': 'HIT'}, path_field='path',
                   execution_environment=execution_environment(typed_context(identity=RunIdentity(None, execution_root=ExecutionRoot(root)))))
    return SimpleNamespace(tool_call_id='s', context=typed_context(identity=RunIdentity(session_id='session', execution_root=ExecutionRoot(root) if root is not None else None), authorization=ToolAuthorization(permission_rules=PermissionSettings(), session_allow=[], permit={'call_id': 's', 'session_id': 'session', 'agent_path': 'root', 'subject': subject})))


def clock_modules(monkeypatch, clock):
    proxy = SimpleNamespace(get_running_loop=lambda: SimpleNamespace(time=lambda: clock[0]),
        to_thread=asyncio.to_thread, current_task=asyncio.current_task, create_task=asyncio.create_task)
    for module in (grep, find, search_protocol):
        monkeypatch.setattr(module, 'asyncio', proxy)


@pytest.mark.parametrize('timeout', [None, 0, 30])
def test_grep_groups_share_one_optional_deadline(monkeypatch, tmp_path, timeout):
    async def scenario():
        clock, requests = [0.0], []
        clock_modules(monkeypatch, clock)
        paths = [tmp_path / ('entry-' + str(i) + '-' + 'x' * 64 + '.txt') for i in range(12)]
        for path in paths:
            path.write_text('HIT\n')
        class Runner:
            async def run(self, request, consume, control=None):
                requests.append(request)
                clock[0] += 2
                if '--files' in request.argv:
                    for path in paths:
                        await consume('stdout', (str(path) + '\0').encode())
                else:
                    for path in request.argv[request.argv.index('--') + 1:]:
                        data = {'type': 'match', 'data': {'path': {'text': path},
                            'line_number': 1, 'lines': {'text': 'HIT\n'}}}
                        await consume('stdout', (json.dumps(data) + '\n').encode())
                return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        result = await grep._grep_files(tmp_path, 'HIT', None, False, False, 0, 100,
            runtime(tmp_path, 'grep'), settings=GrepSettings(max_argument_bytes=512),
            runner=Runner(), timeout=timeout)
        assert len(requests) >= 4 and result.count(':1: HIT') == 12
        values = [request.timeout_seconds for request in requests]
        if timeout:
            assert values == [30 - index * 2 for index in range(len(values))]
        else:
            assert values == [None] * len(values)
    asyncio.run(scenario())


@pytest.mark.parametrize('name', ['grep', 'find'])
def test_positive_timeout_preserves_authorized_partial_results_without_setting_global_stop(monkeypatch, tmp_path, name):
    async def scenario():
        clock = [0.0]
        clock_modules(monkeypatch, clock)
        target = tmp_path / 'HIT.txt'
        target.write_text('HIT\n')
        control = SimpleNamespace(requested=False)
        rt = runtime(tmp_path, name)
        rt.context = replace(rt.context, scope=replace(rt.context.scope, control=control))
        requests = []
        class Runner:
            async def run(self, request, consume, passed_control=None):
                requests.append(request)
                if '--files' in request.argv:
                    await consume('stdout', (str(target) + '\0').encode())
                elif name == 'find':
                    await consume('stdout', (str(target) + '\0').encode())
                    clock[0] = 11.0
                    await consume('stdout', (str(target) + '\0').encode())
                else:
                    record = {'type': 'match', 'data': {'path': {'text': str(target)},
                        'line_number': 1, 'lines': {'text': 'HIT\n'}}}
                    await consume('stdout', (json.dumps(record) + '\n').encode())
                    clock[0] = 11.0
                    await consume('stdout', (json.dumps(record) + '\n').encode())
                return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        if name == 'grep':
            text = await grep._grep_files(tmp_path, 'HIT', None, False, False, 0, 100, rt,
                settings=GrepSettings(), runner=Runner(), timeout=10)
            assert 'HIT.txt:1: HIT' in text
        else:
            text = await find._find_files(tmp_path, '*', 100, rt,
                settings=FindSettings(), runner=Runner(), timeout=10)
            assert text.startswith('HIT.txt\n')
        assert 'incomplete' in text and 'timeout' in text
        assert not control.requested and all(0 < request.timeout_seconds <= 10 for request in requests)
    asyncio.run(scenario())


def test_no_deadline_still_checks_stop_and_positive_deadline_checks_elapsed(monkeypatch):
    async def scenario():
        clock = [1000000.0]
        clock_modules(monkeypatch, clock)
        search_protocol.check_search(None, SimpleNamespace(requested=False))
        with pytest.raises(search_protocol.SearchStopped, match='stopped by the user'):
            search_protocol.check_search(None, SimpleNamespace(requested=True))
        with pytest.raises(search_protocol.SearchStopped, match='timeout'):
            search_protocol.check_search(10, None)
    asyncio.run(scenario())


@pytest.mark.parametrize('name', ['grep', 'find'])
def test_explicit_search_timeout_reaps_owned_native_process_and_keeps_partial_result(monkeypatch, tmp_path, name):
    from dataclasses import replace
    import sys
    from naiyue_test_agent.tools.processes import HostProcessRunner
    async def scenario():
        target = tmp_path / 'HIT.txt'
        target.write_text('HIT\n')
        outcomes = []
        original = HostProcessRunner.run
        async def owned(self, request, consume, control=None):
            if '--files' in request.argv:
                await consume('stdout', (str(target) + '\0').encode())
                return ProcessOutcome(0, 'exited', True, True, True, True, 0)
            record = (str(target) + '\0').encode() if name == 'find' else (json.dumps({
                'type': 'match', 'data': {'path': {'text': str(target)},
                    'line_number': 1, 'lines': {'text': 'HIT\n'}}}) + '\n').encode()
            script = f'import os,time;os.write(1,{record!r});time.sleep(60)'
            # A Python child supplies rg/fd framing on every OS; the runner and
            # its timeout/pipe/consumer settlement are real, no shebang dependency.
            result = await original(self, replace(request, argv=[sys.executable, '-u', '-c', script]), consume, control)
            outcomes.append(result)
            return result
        monkeypatch.setattr(HostProcessRunner, 'run', owned)
        rt = runtime(tmp_path, name)
        if name == 'grep':
            text = await grep._grep_files(tmp_path, 'HIT', None, False, False, 0, 100, rt,
                settings=GrepSettings(), runner=HostProcessRunner(), timeout=1)
            assert 'HIT.txt:1: HIT' in text
        else:
            text = await find._find_files(tmp_path, '*', 100, rt,
                settings=FindSettings(), runner=HostProcessRunner(), timeout=1)
            assert text.startswith('HIT.txt\n')
        assert 'incomplete' in text and 'timeout' in text
        assert len(outcomes) == 1 and outcomes[0].termination == 'timeout'
        assert outcomes[0].process_reaped and outcomes[0].pipes_closed and outcomes[0].consumer_settled
    asyncio.run(scenario())


def test_elapsed_between_precheck_and_request_cannot_send_negative_timeout(monkeypatch, tmp_path):
    async def scenario():
        clock = iter([9.0, 11.0])
        monkeypatch.setattr(search_protocol, 'asyncio', SimpleNamespace(get_running_loop=lambda:
            SimpleNamespace(time=lambda: next(clock)), create_task=asyncio.create_task))
        seen = []
        class Runner:
            async def run(self, request, consume, control=None):
                seen.append(request.timeout_seconds)
                return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        async def receive(record):
            pytest.fail('No records')
        try:
            await search_protocol.stream_records(Runner(), ['owned'], tmp_path, 10.0, None,
                search_protocol.RecordBuffer(b'\0', 10), receive, search_protocol.DiagnosticTail(10))
        except search_protocol.SearchStopped as error:
            assert str(error) == 'timeout' and seen == []
        else:
            assert seen and seen[0] > 0
    asyncio.run(scenario())
