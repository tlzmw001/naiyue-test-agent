"""The privileged service must route probes and jobs through the same identity."""
import asyncio
import os
from pathlib import Path

import pytest
from naiyue_test_agent.contracts.sandbox import SandboxError


def test_task_runner_drops_identity_and_filters_environment(monkeypatch,tmp_path):
    from naiyue_test_agent.envd.identity import TaskProcessRunner
    from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessRequest
    requests=[]
    async def record(self,request,consume,control=None):
        requests.append(request)
        return 'result'
    monkeypatch.setattr(os,'geteuid',lambda:0)
    monkeypatch.setattr(HostProcessRunner,'run',record)
    request=ProcessRequest(['python','-V'],tmp_path,{'PATH':'/usr/bin','NYT_SESSION_ID':'secret','HOME':'/root'})
    assert asyncio.run(TaskProcessRunner().run(request,None)) == 'result'
    assert requests[0].identity == (1000,1000)
    assert 'NYT_SESSION_ID' not in requests[0].env
    assert requests[0].env['HOME'] != '/root'


def test_production_layout_refuses_task_owned_control_state(tmp_path):
    from naiyue_test_agent.envd.identity import check_private_directory
    path=tmp_path/'state';path.mkdir(mode=0o777);path.chmod(0o777)
    with pytest.raises(SandboxError,match='env_policy_mismatch'):check_private_directory(path)


def test_container_policy_rejects_unapproved_extra_capability():
    from naiyue_test_agent.contracts.sandbox import CONTROL_CAPABILITIES
    assert set(CONTROL_CAPABILITIES) == {'SETUID','SETGID','KILL','DAC_OVERRIDE','CHOWN','FOWNER'}


def test_control_git_rejects_task_writable_executable(tmp_path):
    from naiyue_test_agent.envd.identity import trusted_git_executable
    executable=tmp_path/'custom-git'
    executable.write_text('#!/bin/sh\necho task-controlled\n')
    executable.chmod(0o755)
    with pytest.raises(SandboxError,match='env_policy_mismatch'):trusted_git_executable(str(executable))


def test_control_git_resolves_image_binary():
    from naiyue_test_agent.envd.identity import trusted_git_executable
    path=trusted_git_executable('/usr/bin/git')
    assert Path(path).is_absolute() and Path(path).is_file()
