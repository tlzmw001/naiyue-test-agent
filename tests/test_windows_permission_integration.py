"""Real graph decisions with synthetic Windows subjects; no native IO emulation."""

from native_application_helpers import async_test

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.agent_runtime.tool_execution import permissions as pipeline
from naiyue_test_agent.tools import create_builtin_tools, permissions
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.permissions import FilePermission
from naiyue_test_agent.model_access import ModelCatalog
from test_dynamic_tools import DynamicModel


@pytest.mark.parametrize('tool_name,args', [
    ('read', {}), ('ls', {}), ('find', {'pattern': '*'}), ('grep', {'pattern': 'NEEDLE'}),
    ('write', {'content': 'after'}), ('edit', {'edits': [{'oldText': 'before', 'newText': 'after'}]}),
])
@async_test
async def test_extended_windows_target_denied_before_all_file_tool_execution(monkeypatch, tmp_path, tool_name, args):
    environment = {'kind': 'host', 'platform': 'windows'}
    target = r'\\?\C:\work\private.txt'
    prepared = []
    class WindowsPermission(FilePermission):
        def prepare(self, arguments, context):
            prepared.append(dict(arguments))
            return dict(operation=self.operation, workspace='C:/work', requested=target,
                resolved=target, kind='file', anchor={'path': 'C:/work', 'device': 1, 'inode': 2})
    settings = AppSettings.model_validate({'permissions': {
        'mode': 'BypassApprovals', 'deny': [{'tool': tool_name, 'path': 'C:/work/private.txt'}],
        'allow': [{'tool': tool_name, 'path': 'C:/work/**'}]}})
    original = next(spec for spec in create_builtin_tools(settings.tools, process_settings=settings.process, shell_task_settings=settings.shell_tasks, progress_settings=settings.progress) if spec.tool.name == tool_name)
    spec = replace(original, permissions=WindowsPermission('path', original.permissions.operation))
    def never(*args, **kwargs):
        pytest.fail('Denied file tool entered execution')
    async def anever(*args, **kwargs):
        pytest.fail('Denied file tool entered async execution')
    monkeypatch.setattr(spec.tool, 'func', never)
    monkeypatch.setattr(spec.tool, 'coroutine', anever)
    monkeypatch.setattr(pipeline, 'execution_environment', lambda: environment)
    monkeypatch.setattr(permissions, 'execution_environment', lambda: environment)
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[
        {'name': tool_name, 'args': {'path': target, **args}, 'id': 'denied'}]), AIMessage(content='done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    options = dict(tools=[spec], settings=settings, system_prompt='', response_format=None)
    async def scenario():
        app = await build_async_local_application('synthetic', **options)
        sid = await app.acreate_workspace_session('A', tmp_path)
        events = []
        app.subscribe_events(events.append)
        try:
            result = await app.arun(sid, [('user', 'operate')])
            assert len(prepared) == 1
            config = {'configurable': {'thread_id': app._sessions.require(sid).get_thread_id()}}
            snapshots = ([item async for item in app._agent.aget_state_history(config)])
            batches = [item.values['tool_execution']['calls'] for item in snapshots
                       if item.values.get('tool_execution', {}).get('calls')]
            assert batches, 'prepared permission facts must be checkpointed'
            call = batches[0][0]
            assert (call['decision'], call['source']) == ('deny', 'config_deny')
            assert call['subject']['requested'] == target and call['subject']['resolved'] == target
            feedback = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert len(feedback) == 1 and feedback[0].status == 'error' and feedback[0].tool_call_id == 'denied'
            assert result['messages'][-1].content == 'done' and not result.get('approval_requests')
            finished = [event for event in events if event.event_type == 'tool_call_finished']
            assert len(finished) == 1 and finished[0].data.outcome == 'not_executed'
        finally:
            await app.aclose()
    await scenario()
