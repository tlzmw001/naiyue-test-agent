"""A protected process cleanup failure has session-wide, shell-neutral diagnostics."""

import asyncio

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.tools.permissions import ToolPermission
from naiyue_test_agent.tools.processes import ProcessCleanupError


@pytest.mark.parametrize('async_mode', [False, True])
@pytest.mark.parametrize('approval', [False, True])
def test_cleanup_failure_uses_session_diagnostic_and_preserves_control(
    monkeypatch, caplog, async_mode, approval,
):
    async def scenario():
        failure = ProcessCleanupError('PRIVATE_CLEANUP_DETAIL')

        @tool
        def custom_process() -> str:
            """Report a protected synthetic process settlement failure."""
            raise failure

        spec = ToolSpec(custom_process, custom_process.args_schema,
                        permissions=ToolPermission(default='ask' if approval else 'allow'))
        reply = AIMessage(content='', tool_calls=[
            {'name': 'custom_process', 'args': {}, 'id': 'custom-1'},
        ])
        if async_mode:
            from test_async_application import StreamingToolModel, make_app
            app = await make_app(monkeypatch, StreamingToolModel(replies=[reply]), [spec])
            session = await app.acreate_session('A')
        else:
            from test_application_run_events import ToolModel, make_app
            app = make_app(monkeypatch, ToolModel(responses=[reply]), [spec])
            session = app.create_session('A')
        events = []
        app.subscribe_events(events.append)
        try:
            if approval:
                pending = (await app.arun(session, [('user', 'run')]) if async_mode
                           else app.run(session, [('user', 'run')]))
                assert [item['tool_call_id'] for item in pending['approval_requests']] == ['custom-1']
            with pytest.raises(ProcessCleanupError) as caught:
                if async_mode:
                    if approval:
                        await app.aresume_tool_approval(session, ['approve'])
                    else:
                        await app.arun(session, [('user', 'run')])
                elif approval:
                    app.resume_tool_approval(session, ['approve'])
                else:
                    app.run(session, [('user', 'run')])
            assert caught.value is failure
            state = app._sessions._sessions[session]
            assert state['manual_review_required'] and not state['busy']
            assert state['pending_tool_approval'] is None
            end = [event for event in events if event.event_type == 'run_finished'][-1]
            assert end.data.status == 'failed' and end.data.manual_review_required
            diagnostics = [record for record in caplog.records
                           if record.getMessage() == 'session_manual_review_required']
            assert len(diagnostics) == 1
            assert getattr(diagnostics[0], 'session_event', None) == {
                'event': 'session_manual_review_required', 'session_id': session, 'run_id': end.run_id,
            }
            assert not hasattr(diagnostics[0], 'bash_event')
            assert 'PRIVATE_CLEANUP_DETAIL' not in caplog.text
        finally:
            if async_mode:
                await app.aclose()
    asyncio.run(scenario())
