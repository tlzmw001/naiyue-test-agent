"""A host process is terminated, but its already-started effects remain uncertain."""

import asyncio
import os
import shlex
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.tools import create_session_bash_tool
from naiyue_test_agent.agent_runtime.tools.bash import BashOutcomeUnknown
from test_async_application import make_app, StreamingToolModel, call


def test_user_stop_terminates_bash_and_preserves_partial_output(monkeypatch, tmp_path):
    async def scenario():
        marker = tmp_path / 'pid'
        script = f"import os,time;open({str(marker)!r},'w').write(str(os.getpid()));print('started',flush=True);time.sleep(60)"
        command = f'{shlex.quote(sys.executable)} -c {shlex.quote(script)}'
        app = await make_app(monkeypatch, StreamingToolModel(replies=[
            call('bash', {'command': command}), AIMessage(content='must not run')]),
            [create_session_bash_tool()])
        session = await app.acreate_workspace_session('A', tmp_path)
        task = None
        pid = None
        try:
            first = await app.arun(session, [('user', 'run')])
            assert first['approval_requests']
            task = asyncio.create_task(app.aresume_tool_approval(session, ['approve']))
            async with asyncio.timeout(5):
                while not marker.exists() or not marker.read_text():
                    await asyncio.sleep(0.01)
            pid = int(marker.read_text())
            app.request_stop(session)
            with pytest.raises(BashOutcomeUnknown) as caught:
                await asyncio.wait_for(task, 5)
            assert 'stopped by the user' in str(caught.value)
            assert 'started' in caught.value.partial_stdout
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            assert app._sessions._sessions[session]['manual_review_required']
            with pytest.raises(application.SessionManualReviewRequiredError):
                await app.arun(session, [('user', 'no automatic retry')])
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
