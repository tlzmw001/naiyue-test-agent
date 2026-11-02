"""Immutable input objects are reusable; per-import local provenance stays distinct."""
import pytest
from naiyue_test_agent.contracts.cloud_inputs import InputLimits,Selection
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def test_local_cache_reuses_input_across_codecs_without_changing_source(tmp_path):
    from naiyue_test_agent.cloud_files.cache import capture_cached
    root=tmp_path/'source';root.mkdir();(root/'api.proto').write_bytes(b'raw\r\nproto')
    selection=Selection(paths=['api.proto']);cache=tmp_path/'cache'
    first=capture_cached(root,selection,cache,InputLimits(),codec='gzip')
    second=capture_cached(root,selection,cache,InputLimits(),codec='tar')
    assert first.input_id==second.input_id and first.directory==second.directory and second.codec=='gzip'
    assert first.archive_sha256==second.archive_sha256 and (root/'api.proto').read_bytes()==b'raw\r\nproto'
    assert not list((cache/'staging').iterdir())


def test_local_cache_rejects_foreign_root_and_budget_without_ready_object(tmp_path):
    from naiyue_test_agent.cloud_files.cache import capture_cached
    root=tmp_path/'source';root.mkdir();(root/'data').write_bytes(b'data')
    cache=tmp_path/'foreign';cache.mkdir();(cache/'user-file').write_text('preserve')
    with pytest.raises(CloudError,match='input_cache_foreign'):
        capture_cached(root,Selection(paths=['data']),cache,InputLimits())
    assert (cache/'user-file').read_text()=='preserve'
    with pytest.raises(CloudError,match='input_cache_capacity'):
        capture_cached(root,Selection(paths=['data']),tmp_path/'cache',InputLimits(max_cache_bytes=1))
    assert not list((tmp_path/'cache/inputs').glob('*'))


def test_cache_checks_actual_frozen_size_before_publish(tmp_path,monkeypatch):
    import naiyue_test_agent.cloud_files.cache as module
    root=tmp_path/'source';root.mkdir();source=root/'data';source.write_bytes(b'x');original=module.capture
    def grows(*args,**kwargs):
        source.write_bytes(b'x'*(5*1024*1024))
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'capture',grows)
    with pytest.raises(CloudError,match='input_cache_capacity'):
        module.capture_cached(root,Selection(paths=['data']),tmp_path/'cache',InputLimits(max_cache_bytes=3*1024*1024),codec='tar')
    assert not list((tmp_path/'cache/inputs').iterdir())


def test_cache_initialization_retries_after_marker_write_failure(tmp_path,monkeypatch):
    import naiyue_test_agent.cloud_files.cache as module
    root=tmp_path/'source';root.mkdir();(root/'data').write_bytes(b'x');cache=tmp_path/'cache'
    with monkeypatch.context() as patch:
        original=module.write_json
        def fails(path,value):
            if path.name=='cache.json':raise OSError('controlled marker failure')
            return original(path,value)
        patch.setattr(module,'write_json',fails)
        with pytest.raises(OSError):module.capture_cached(root,Selection(paths=['data']),cache,InputLimits())
    assert module.capture_cached(root,Selection(paths=['data']),cache,InputLimits()).directory.is_dir()


def test_cache_signature_handles_windows_lock_byte_initialization(tmp_path,monkeypatch):
    import os
    import naiyue_test_agent.cloud_files.cache as module
    root=tmp_path/'source';root.mkdir();(root/'data').write_bytes(b'x');original=module.acquire_store_lock
    def windows_lock_byte(fd):
        if os.fstat(fd).st_size==0:os.write(fd,b'\0')
        return original(fd)
    monkeypatch.setattr(module,'acquire_store_lock',windows_lock_byte)
    assert module.capture_cached(root,Selection(paths=['data']),tmp_path/'cache',InputLimits()).directory.is_dir()


@pytest.mark.parametrize('prefix',[b'',b'nyt-input-'])
def test_initial_signature_failure_is_recoverable(tmp_path,monkeypatch,prefix):
    import os
    import naiyue_test_agent.cloud_files.cache as module
    root=tmp_path/'source';root.mkdir();(root/'data').write_bytes(b'x');cache=tmp_path/'cache';original=os.write
    with monkeypatch.context() as patch:
        def fail(fd,data):
            if data==b'nyt-input-cache-init-v1\n':
                if prefix:original(fd,prefix)
                raise OSError('controlled init signature failure')
            return original(fd,data)
        patch.setattr(module.os,'write',fail)
        with pytest.raises(OSError):module.capture_cached(root,Selection(paths=['data']),cache,InputLimits())
    assert module.capture_cached(root,Selection(paths=['data']),cache,InputLimits()).directory.is_dir()


def test_cache_final_source_receipt_bytes_are_within_quota(tmp_path,monkeypatch):
    import naiyue_test_agent.cloud_files.cache as module
    from naiyue_test_agent.cloud_files.sources import select_git
    from test_git_source_plan import repository
    root=repository(tmp_path);source=root/'data';source.write_bytes(b'x');plan=select_git(root,InputLimits())
    capture=module.capture;used=module._used;observed=[]
    def grows(*args,**kwargs):
        source.write_bytes(b'x'*(3*1024*1024));return capture(*args,**kwargs)
    def measure(path):
        value=used(path);observed.append(value);return value
    monkeypatch.setattr(module,'capture',grows);monkeypatch.setattr(module,'_used',measure)
    module.capture_cached(root,plan.selection,tmp_path/'cache-a',InputLimits(),codec='tar',source_receipt=plan.receipt)
    limit=observed[-1];source.write_bytes(b'x');cache=tmp_path/'cache-b'
    try:module.capture_cached(root,plan.selection,cache,InputLimits(max_cache_bytes=limit),codec='tar',source_receipt=plan.receipt)
    except CloudError as error:assert error.code=='input_cache_capacity'
    assert used(cache)<=limit
