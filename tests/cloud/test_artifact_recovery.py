"""Crash boundaries retain exact frozen bytes and reject unrelated spool facts."""
import asyncio
from pathlib import Path

import pytest

from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.cloud_runner.state import write_json,read_json
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from test_runner_kernel import bootstrap
from test_artifact_capture import command


@pytest.mark.parametrize('tamper',[False,True])
def test_capture_published_before_journal_ack_is_reconciled_without_rescan(tmp_path,tamper):
    async def scenario():
        b=bootstrap();root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        source=root/'report';source.write_bytes(b'first frozen bytes');cmd=command('report')
        async with open_runner(b,root,state) as k:
            original=k.artifacts.journal.save
            def fail_once(value):
                if value.status=='captured':raise OSError('controlled journal ack loss')
                return original(value)
            k.artifacts.journal.save=fail_once
            with pytest.raises(OSError):await k.artifacts.capture(cmd)
            assert k.artifacts.journal.get(cmd.command_id).status=='unknown' and k.phase=='blocked'
        source.write_bytes(b'new source must never be rescanned')
        if tamper:
            path=state/'artifacts'/cmd.artifact_id/'artifact.json'
            value=read_json(path);value['task_id']='f'*32;write_json(path,value)
        if tamper:
            with pytest.raises(CloudError,match='artifact_spool_identity_mismatch'):
                async with open_runner(b,root,state):pass
        else:
            async with open_runner(b,root,state) as k:
                value=await k.artifacts.capture(cmd)
                assert value.status=='captured' and k.phase=='blocked'
                assert k.artifacts.blob(cmd.artifact_id).read_bytes()==b'first frozen bytes'
    asyncio.run(scenario())


def test_capacity_rejection_is_durable_queryable_failure_not_unknown(tmp_path):
    from naiyue_test_agent.contracts.cloud_artifacts import ArtifactLimits
    async def scenario():
        b=bootstrap().model_copy(update={'artifact_limits':ArtifactLimits(max_artifacts=1)})
        root=tmp_path/'project';root.mkdir();(root/'report').write_bytes(b'bytes');state=tmp_path/'state';state.mkdir(mode=0o700)
        rejected=command('report')
        async with open_runner(b,root,state) as k:
            first=await k.artifacts.capture(command('report'));k.artifacts.available(first)
            result=await k.artifacts.capture(rejected)
            assert result.status=='failed' and result.error_code=='artifact_capacity' and k.phase=='waiting_model'
            assert k.artifacts.journal.get(rejected.command_id)==result
        async with open_runner(b,root,state) as k:
            assert await k.artifacts.capture(rejected)==result
    asyncio.run(scenario())
