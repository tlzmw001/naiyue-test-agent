"""Stop while approval preparation is waiting must not leave a stale interrupt."""

from naiyue_test_agent.tools import ToolSpec

import asyncio
from threading import Event

from langchain.tools import tool
from langchain_core.messages import ToolMessage

from naiyue_test_agent.tools.permissions import ToolPermission
from test_async_application import StreamingToolModel, make_app, call


def test_stop_during_approval_hook_finishes_without_executing_tool(monkeypatch, tmp_path):
    async def scenario():
        entered, release, executed = Event(), Event(), Event()

        @tool
        def gated() -> str:
            """Never execute after stop during review."""
            executed.set()
            return 'unexpected'

        class WaitingPermission(ToolPermission):
            def default_decision(self, subject):
                entered.set()
                assert release.wait(5)
                return 'ask'

        app = await make_app(monkeypatch, StreamingToolModel(replies=[call('gated', {})]), [ToolSpec(gated, gated.args_schema, permissions=WaitingPermission())])
        session = await app.acreate_workspace_session('A', tmp_path)
        task = asyncio.create_task(app.arun(session, [('user', 'start')]))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            app.request_stop(session)
            release.set()
            result = await asyncio.wait_for(task, 5)
            assert result['run_stopped'] and '__interrupt__' not in result
            assert app.pending_tool_approvals(session) == [] and not executed.is_set()
            result_message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert result_message.status == 'error' and 'user stopped' in result_message.content
            snapshot = await app._agent.aget_state({'configurable': {
                'thread_id': app._sessions.get_thread_id(session)}})
            assert not snapshot.next and not any(t.interrupts for t in snapshot.tasks)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())
