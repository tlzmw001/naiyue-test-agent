import asyncio
import os
from pathlib import Path
from threading import Event

import httpx
import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.cloud_runner.input_install import install_input
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import settings,owner_headers
from test_input_formats import package
from test_input_import import transfer_request


def uploaded(runtime,captured):
    receipt=runtime.inputs.create(transfer_request(captured));transfer=runtime.inputs.transfer(receipt.resource_id)
    (runtime.inputs.staging(transfer.transfer_id)/'archive').write_bytes((captured.directory/'archive').read_bytes())
    return runtime.inputs.repository.save(transfer,transfer.model_copy(update={'state':'uploaded','offset':transfer.size}))


def test_commit_after_publish_retries_same_transfer_without_reupload(tmp_path,monkeypatch):
    captured,_,_=package(tmp_path)
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        transfer=uploaded(runtime,captured)
        with monkeypatch.context() as patch:
            def fail(*a):raise OSError('controlled DB failure after directory publish')
            patch.setattr(runtime.inputs.repository,'commit',fail)
            with pytest.raises(OSError):runtime.inputs.commit(transfer.transfer_id)
        assert (runtime.settings.data_dir/'inputs'/captured.input_id/'archive').exists()
        assert runtime.inputs.commit(transfer.transfer_id).input_id==captured.input_id
        assert runtime.inputs.transfer(transfer.transfer_id).state=='committed'


def test_installer_prepared_before_rename_resumes(tmp_path,monkeypatch):
    captured,_,_=package(tmp_path);work=tmp_path/'work';state=tmp_path/'state';work.mkdir();state.mkdir()
    original=os.rename
    def interrupted(source,target):
        if Path(source).name=='.nyt-input-staging':raise OSError('controlled interruption before project rename')
        return original(source,target)
    with monkeypatch.context() as patch:
        patch.setattr(os,'rename',interrupted)
        with pytest.raises(CloudError):install_input(captured.directory,work,state,captured.input_id,InputLimits())
    assert (work/'.nyt-input-staging').exists()
    assert install_input(captured.directory,work,state,captured.input_id,InputLimits())['status']=='installed'


def test_cancelled_http_commit_keeps_exclusive_until_worker_finishes(tmp_path,monkeypatch):
    captured,_,_=package(tmp_path)
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        transfer=uploaded(runtime,captured);entered,release=Event(),Event();original=runtime.inputs.repository.commit
        def paused(*args):
            entered.set();assert release.wait(10)
            return original(*args)
        monkeypatch.setattr(runtime.inputs.repository,'commit',paused)
        async def run():
            owner,_=create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=runtime.settings.origin,headers=owner_headers(runtime)) as c:
                job=asyncio.create_task(c.post('/api/cloud/v1/transfers/'+transfer.transfer_id+'/commit',json={}))
                await asyncio.to_thread(entered.wait)
                job.cancel()
                # A loop checkpoint lets cancellation unwind, without a tight sleep.
                await asyncio.sleep(0)
                cancelled=await c.delete('/api/cloud/v1/transfers/'+transfer.transfer_id)
                release.set()
                with pytest.raises(asyncio.CancelledError):await job
                assert cancelled.status_code==409
                await runtime.inputs.aclose()
                assert runtime.inputs.get_input(captured.input_id).input_id==captured.input_id
                assert runtime.inputs.transfer(transfer.transfer_id).state=='committed'
        try:asyncio.run(run())
        finally:release.set()


def test_ready_archive_same_length_replacement_is_refused(tmp_path):
    captured,_,_=package(tmp_path)
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        transfer=uploaded(runtime,captured);runtime.inputs.commit(transfer.transfer_id)
        archive=runtime.settings.data_dir/'inputs'/captured.input_id/'archive';replacement=archive.with_name('replacement')
        replacement.write_bytes(b'x'*archive.stat().st_size);os.replace(replacement,archive)
        with pytest.raises(CloudError):runtime.inputs.get_input(captured.input_id)


def test_cancel_after_published_commit_failure_reconciles_instead_of_orphaning(tmp_path,monkeypatch):
    captured,_,_=package(tmp_path)
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        transfer=uploaded(runtime,captured)
        with monkeypatch.context() as patch:
            def fail(*a):raise OSError('controlled DB failure after publish')
            patch.setattr(runtime.inputs.repository,'commit',fail)
            with pytest.raises(OSError):runtime.inputs.commit(transfer.transfer_id)
        with pytest.raises(CloudError,match='input_already_committed'):runtime.inputs.cancel(transfer.transfer_id)
        assert runtime.inputs.transfer(transfer.transfer_id).state=='committed'
        assert runtime.inputs.get_input(captured.input_id).input_id==captured.input_id
