"""Only the internal transport supplies an owned finite input file descriptor."""
import asyncio
import os
import sys
import tempfile
from naiyue_test_agent.tools.processes import ProcessRequest,HostProcessRunner


def test_owned_transport_input_uses_existing_cleanup_and_bounded_output(tmp_path):
    async def scenario():
        output=[]
        async def consume(stream,chunk):output.append(chunk)
        with tempfile.TemporaryFile() as source:
            source.write(b'finite-json-frame\n');source.seek(0)
            request=ProcessRequest([sys.executable,'-c','import sys;print(sys.stdin.read())'],tmp_path,
                dict(os.environ),timeout_seconds=5,stdin_fd=source.fileno())
            outcome=await HostProcessRunner().run(request,consume)
            assert outcome.returncode==0 and outcome.pipes_closed and outcome.consumer_settled
            assert b'finite-json-frame' in b''.join(output)
    asyncio.run(scenario())


def test_background_cannot_borrow_a_transport_owned_descriptor(tmp_path):
    from dataclasses import replace
    import pytest
    from langchain_core.tools import ToolException
    from naiyue_test_agent.application.sessions.background_tasks import BackgroundProcessManager
    from naiyue_test_agent.execution_roots import ExecutionRoot
    from runtime_context_helpers import host_files
    from test_process_lifecycle import request
    original=request(tmp_path,'')
    with pytest.raises(ToolException,match='transport input'):
        BackgroundProcessManager._validate_binding(ExecutionRoot(tmp_path),None,tmp_path,
            host_files(tmp_path).environment,replace(original,stdin_fd=0),tmp_path.parent/'archives')
