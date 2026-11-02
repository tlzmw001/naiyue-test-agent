"""Freeze one explicit file once; replay never observes a newer workspace."""
import asyncio
import hashlib
import os
from uuid import uuid4

import pytest

from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from test_runner_kernel import bootstrap


def command(path='reports/report.bin',**changes):
    from naiyue_test_agent.contracts.cloud_artifacts import CaptureArtifact
    return CaptureArtifact(command_id=str(uuid4()),artifact_id=str(uuid4()),relative_name=path,**changes)


@pytest.mark.parametrize('data',[b'',b'\x00\xff\r\n'*16384],ids=['empty','binary'])
def test_capture_is_immutable_and_reopens_without_model(tmp_path,data):
    async def scenario():
        b=bootstrap();root=tmp_path/'project';root.mkdir();(root/'reports').mkdir()
        source=root/'reports/report.bin';source.write_bytes(data);state=tmp_path/'state';state.mkdir(mode=0o700)
        cmd=command()
        async with open_runner(b,root,state) as kernel:
            result=await kernel.artifacts.capture(cmd)
            assert result.status=='captured' and kernel.phase=='blocked'
            assert result.artifact.size==len(data) and result.artifact.sha256==hashlib.sha256(data).hexdigest()
            assert kernel.artifacts.blob(cmd.artifact_id).read_bytes()==data
            source.write_bytes(b'new data')
            assert await kernel.artifacts.capture(cmd)==result
            with pytest.raises(CloudError,match='command_id_conflict'):
                await kernel.artifacts.capture(cmd.model_copy(update={'relative_name':'other.bin'}))
        async with open_runner(b,root,state) as kernel:
            assert await kernel.artifacts.capture(cmd)==result
            assert kernel.artifacts.blob(cmd.artifact_id).read_bytes()==data
    asyncio.run(scenario())


@pytest.mark.parametrize('kind',['symlink','fifo','parent_link'])
def test_artifact_capture_rejects_untrusted_file_types_and_parents(tmp_path,kind):
    async def scenario():
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        outside=tmp_path/'outside';outside.mkdir();(outside/'secret').write_bytes(b'private fixture')
        if kind=='parent_link':(root/'reports').symlink_to(outside,target_is_directory=True);path='reports/secret'
        elif kind=='symlink':(root/'report').symlink_to(outside/'secret');path='report'
        else:os.mkfifo(root/'report');path='report'
        async with open_runner(bootstrap(),root,state) as kernel:
            result=await kernel.artifacts.capture(command(path))
            assert result.status=='failed' and result.artifact is None and result.error_code
            assert kernel.phase=='waiting_model'
    asyncio.run(scenario())


def test_capture_rejects_changed_source_without_publishing(tmp_path,monkeypatch):
    import naiyue_test_agent.cloud_runner.artifacts as module
    async def scenario():
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        source=root/'report';source.write_bytes(b'original')
        original=module.source_handle
        from contextlib import contextmanager
        @contextmanager
        def changed(*args):
            with original(*args) as value:
                yield value
                source.write_bytes(b'changed')
        monkeypatch.setattr(module,'source_handle',changed)
        async with open_runner(bootstrap(),root,state) as kernel:
            result=await kernel.artifacts.capture(command('report'))
            assert result.status=='failed' and result.artifact is None
    asyncio.run(scenario())


def test_failed_capture_staging_consumes_local_quota(tmp_path,monkeypatch):
    import naiyue_test_agent.cloud_runner.artifacts as module
    from contextlib import contextmanager
    from naiyue_test_agent.contracts.cloud_artifacts import ArtifactLimits
    async def scenario():
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        source=root/'report';source.write_bytes(b'12345678');original=module.source_handle
        @contextmanager
        def changed(*args):
            with original(*args) as value:
                yield value
                source.write_bytes(b'changed!')
        monkeypatch.setattr(module,'source_handle',changed)
        b=bootstrap().model_copy(update={'artifact_limits':ArtifactLimits(max_cache_bytes=8,max_artifacts=2)})
        async with open_runner(b,root,state) as k:
            assert (await k.artifacts.capture(command('report'))).status=='failed'
            second=await k.artifacts.capture(command('report'))
            assert second.status=='failed' and second.error_code=='artifact_capacity'
            assert sum(p.stat().st_size for p in state.glob('artifact-staging/*/bytes'))==8
            third=await k.artifacts.capture(command('report'))
            assert third.status=='failed' and third.error_code=='artifact_capacity'
            assert sum(p.stat().st_size for p in state.glob('artifact-staging/*/bytes'))==8
    asyncio.run(scenario())


def test_capacity_during_copy_still_consumes_artifact_slot(tmp_path,monkeypatch):
    import naiyue_test_agent.cloud_runner.artifacts as module
    from contextlib import contextmanager
    from naiyue_test_agent.contracts.cloud_artifacts import ArtifactLimits
    async def scenario():
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        source=root/'report';source.write_bytes(b'');original=module.source_handle
        @contextmanager
        def grows_after_open(*args):
            with original(*args) as value:
                source.write_bytes(b'12345678')
                yield value
        monkeypatch.setattr(module,'source_handle',grows_after_open)
        b=bootstrap().model_copy(update={'artifact_limits':ArtifactLimits(max_cache_bytes=4,max_artifacts=1,io_bytes=4)})
        async with open_runner(b,root,state) as k:
            for _ in range(3):
                source.write_bytes(b'')
                result=await k.artifacts.capture(command('report'))
                assert result.status=='failed' and result.error_code=='artifact_capacity'
            files=list(state.glob('artifact-staging/*/bytes'))
            assert len(files)==1 and files[0].stat().st_size==4
    asyncio.run(scenario())
