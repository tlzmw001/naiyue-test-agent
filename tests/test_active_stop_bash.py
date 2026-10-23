"""User stop reaps Bash and pairs its error result without another model call."""

import asyncio
import os
import shlex
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.tools import create_session_bash_tool
from test_async_application import make_app, StreamingToolModel, call


def test_user_stop_terminates_bash_and_preserves_partial_output(monkeypatch, tmp_path):
    async def scenario():
        marker = tmp_path / 'pid'
        script = f"import os,time;open({str(marker)!r},'w').write(str(os.getpid()));print('started',flush=True);time.sleep(60)"
        command = f'{shlex.quote(sys.executable)} -c {shlex.quote(script)}'
        app = await make_app(monkeypatch, StreamingToolModel(replies=[
            call('bash', {'command': command}), AIMessage(content='must not run')]),
            [create_session_bash_tool()])
        session = await app.sessions.create_workspace('A', tmp_path)
        task = None
        pid = None
        try:
            first = await app.runs.start(session, [('user', 'run')])
            assert first['approval_requests']
            task = asyncio.create_task(app.runs.resume_approval(session, ['approve']))
            async with asyncio.timeout(5):
                while not marker.exists() or not marker.read_text():
                    await asyncio.sleep(0.01)
            pid = int(marker.read_text())
            app.runs.stop(session)
            result = await asyncio.wait_for(task, 5)
            message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert message.status == 'error' and message.tool_call_id == 't'
            assert 'stopped by the user' in message.content and 'started' in message.content
            assert result['run_stopped']
            assert not any(m.content == 'must not run' for m in result['messages'])
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            assert not app.sessions._sessions.require(session).describe_session()['manual_review_required']
            assert not app.sessions._sessions.require(session).describe_session()['busy']
        finally:
            if pid is not None:
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())
