"""Own nested search children when stopped or directly cancelled."""

from runtime_context_helpers import ExecutionRoot, RunIdentity, ToolAuthorization, typed_context
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.tools import grep as module
from naiyue_test_agent.tools import GrepSettings
from naiyue_test_agent.tools.paths import prepare_file_subject
from naiyue_test_agent.tools.permissions import execution_environment
from naiyue_test_agent.tools.permission_rules import PermissionSettings
from naiyue_test_agent.tools.processes import HostProcessRunner
from test_async_application import make_app, StreamingToolModel, call


def test_internal_process_wait_cancellation_is_not_search_cancellation(tmp_path):
    async def scenario():
        from naiyue_test_agent.tools.search_protocol import stream_records, RecordBuffer, DiagnosticTail
        from naiyue_test_agent.tools.processes import ProcessOutcome
        control = module._SearchControl(None)
        observed = []
        class Runner:
            async def run(self, request, consume, passed_control):
                task = asyncio.current_task()
                task.cancel()  # The transient cancel/uncancel used by an internal asyncio timeout.
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    observed.append(passed_control.requested)
                    task.uncancel()
                return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        async def receive(record):
            pytest.fail('No records are produced')
        # This is an owned synthetic runner, not an OS process or timing-window test.
        await stream_records(Runner(), ['synthetic'], tmp_path, asyncio.get_running_loop().time() + 5,
            control, RecordBuffer(b'\0', 32), receive, DiagnosticTail(32))
        assert observed == [False]
    asyncio.run(scenario())


def owned_native_program(monkeypatch, root):
    target, marker, binary = root / 'note.txt', root / 'started', root / 'native-search'
    target.write_text('HIT\n')
    candidates = [target]
    for index in range(16):
        path = root / f'other-{index:02d}.txt'
        path.touch()
        candidates.append(path)
    names = b'\0'.join(os.fsencode(path) for path in candidates) + b'\0'
    event = {'type': 'match', 'data': {'path': {'text': str(target)}, 'line_number': 1,
                                     'lines': {'text': 'HIT\n'}}}
    binary.write_text(f'#!{sys.executable}\nimport os,sys,time\n'
        f'if "--files" in sys.argv:\n os.write(1,{names!r})\n'
        f'else:\n print({json.dumps(event)!r},flush=True)\n'
        f' open({str(marker)!r},"w").write(str(os.getpid()))\n time.sleep(60)\n')
    binary.chmod(0o700)
    resolve = module.resolve_tool_binary
    monkeypatch.setattr(module, 'resolve_tool_binary', lambda name: str(binary) if name == 'rg' else resolve(name))
    return marker


async def started_pid(marker):
    async with asyncio.timeout(5):
        while not marker.exists() or not marker.read_text():
            await asyncio.sleep(0.01)
    return int(marker.read_text())


def test_direct_cancel_reaps_search_child_inside_discovery_consumer(monkeypatch, tmp_path):
    async def scenario():
        marker = owned_native_program(monkeypatch, tmp_path)
        subject = prepare_file_subject('.', tmp_path, 'search')
        subject.update(tool_name='grep', args={'path': '.', 'pattern': 'HIT'}, path_field='path',
                       execution_environment=execution_environment())
        runtime = SimpleNamespace(tool_call_id='g', context=typed_context(identity=RunIdentity(session_id='s', execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None), authorization=ToolAuthorization(permission_rules=PermissionSettings(), session_allow=[], permit={'call_id': 'g', 'session_id': 's', 'agent_path': 'root', 'subject': subject})))
        task = asyncio.create_task(module._grep_files(tmp_path, 'HIT', '*.txt', False, False, 0, 100,
            runtime, settings=GrepSettings(max_argument_bytes=1024), runner=HostProcessRunner()))
        pid = None
        try:
            pid = await started_pid(marker)
            task.cancel('original-search-cancel')
            with pytest.raises(asyncio.CancelledError) as caught:
                await asyncio.wait_for(task, 8)
            assert caught.value.args == ('original-search-cancel',)
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if pid is not None:
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_active_stop_keeps_partial_result_and_ends_without_next_model(monkeypatch, tmp_path):
    async def scenario():
        marker = owned_native_program(monkeypatch, tmp_path)
        received = asyncio.Event()
        on_event = module._GrepSearch._on_event
        async def observed(self, *args):
            await on_event(self, *args)
            if self.rows:
                received.set()
        monkeypatch.setattr(module._GrepSearch, '_on_event', observed)
        model = StreamingToolModel(replies=[call('grep', {'pattern': 'HIT', 'glob': '*.txt'}),
                                            AIMessage(content='must not run')])
        app = await make_app(monkeypatch, model, [module.create_session_grep_tool()])
        sid = await app.acreate_workspace_session('A', tmp_path)
        pid = None
        task = asyncio.create_task(app.arun(sid, [('user', 'search')]))
        try:
            pid = await started_pid(marker)
            await asyncio.wait_for(received.wait(), 5)
            app.request_stop(sid)
            result = await asyncio.wait_for(task, 8)
            message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert message.tool_call_id == 't' and 'note.txt:1: HIT' in message.content
            assert 'incomplete' in message.content and 'stopped by the user' in message.content
            assert result['run_stopped'] and len(model.seen) == 1
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if app._sessions.require(sid).describe_session()['busy']:
                app.request_stop(sid)
            if pid is not None:
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())
