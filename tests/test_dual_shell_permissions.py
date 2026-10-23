"""Two Shell names share batch approval but never share exact-command rules."""

import asyncio

from langchain_core.messages import AIMessage, ToolMessage


def test_both_shells_wait_for_whole_batch_and_keep_original_names(monkeypatch, tmp_path):
    async def scenario():
        from naiyue_test_agent.tools import create_session_bash_tool, create_session_powershell_tool
        from naiyue_test_agent.tools.powershell import PowerShellAdapter
        from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessOutcome
        from test_async_application import StreamingToolModel, make_app
        seen = []
        async def run(self, request, consume, control):
            if request.argv[-1] == '--version' or request.argv[-1] == '$PSVersionTable.PSVersion.ToString()':
                await consume('stdout', b'7.4.6')
            else:
                seen.append(request)
                await consume('stdout', b'approved output')
            return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        monkeypatch.setattr(HostProcessRunner, 'run', run)
        monkeypatch.setattr(PowerShellAdapter, 'resolve_executable', lambda *_: '/synthetic/pwsh')
        specs = [create_session_bash_tool(), create_session_powershell_tool()]
        model = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
            {'name': 'bash', 'id': 'bash-call', 'args': {'command': 'printf ok'}},
            {'name': 'powershell', 'id': 'ps-call', 'args': {'command': 'Write-Output ok'}},
        ]), AIMessage(content='done')])
        app = await make_app(monkeypatch, model, specs)
        sid = await app.sessions.create_workspace('A', tmp_path)
        events = []
        app.observability.subscribe(events.append)
        try:
            pending = await app.runs.start(sid, [('user', 'run both')])
            assert [r['tool_name'] for r in pending['approval_requests']] == ['bash', 'powershell']
            assert seen == []
            result = await app.runs.resume_approval(sid, ['approve', 'approve'])
            messages = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert [(m.name, m.tool_call_id, m.status) for m in messages] == [
                ('bash', 'bash-call', 'success'), ('powershell', 'ps-call', 'success')]
            assert len(seen) == 2 and all(req.merge_output for req in seen)
            assert len([e for e in events if e.event_type == 'tool_call_finished']) == 2
            assert events[-1].data.status == 'completed'
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_bash_exact_allow_does_not_authorize_powershell_or_suffix(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import create_session_bash_tool, create_session_powershell_tool
    from naiyue_test_agent.tools.permission_rules import PermissionRule, match_rules
    rules = (PermissionRule(tool='bash', args={'command': 'same literal'}),)
    for spec in (create_session_bash_tool(), create_session_powershell_tool()):
        for command in ('same literal', 'same literal; extra'):
            subject = {'tool_name': spec.tool.name, 'args': {'command': command}}
            assert bool(match_rules(rules, subject, deny=False)) == (spec.tool.name == 'bash' and command == 'same literal')
            assert spec.permissions.default_decision(subject) == 'ask'


def test_cli_powershell_approval_displays_workspace_and_escaped_command():
    from naiyue_test_agent.cli.cli_approval import show_approval_batches
    lines = []
    show_approval_batches([{'approval_batch_id': 'batch', 'agent_path': 'root', 'items': [{
        'tool_name': 'powershell', 'target': 'Write-Output "中"\n\x1b[31m',
        'workspace': 'C:\\work',
    }]}], lines.append)
    assert 'powershell 工作区=' in lines[1] and '命令=' in lines[1]
    assert '\\n' in lines[1] and '\\u001b' in lines[1] and '\x1b' not in lines[1]
