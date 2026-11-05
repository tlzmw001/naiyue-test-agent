"""The isolated restore removes only proven regenerable physical-identity caches."""
from uuid import uuid4
import json
import pytest
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def test_restore_preserves_raw_wal_and_rebuilds_baseline_cache(tmp_path):
    from naiyue_test_agent.cloud_runner.upgrade_backup import backup
    from naiyue_test_agent.cloud_runner.restore_backup import restore
    workspace=tmp_path/'workspace';state=tmp_path/'state';workspace.mkdir();state.mkdir()
    (workspace/'run.sh').write_bytes(b'echo fixture');(workspace/'run.sh').chmod(0o755)
    (state/'session.sqlite').write_bytes(b'opaque SQL');(state/'session.sqlite-wal').write_bytes(b'WAL')
    cache=state/'results/.baseline-cache/input';cache.mkdir(parents=True);(cache/'baseline.json').write_bytes(b'old physical identities')
    request=dict(command_id=str(uuid4()),task_id='a'*32,generation=2,reservation_sha256='b'*64)
    source=tmp_path/'backup';receipt=backup(workspace,state,source,request,max_bytes=10000,max_files=100)
    out=tmp_path/'restored';out.mkdir();(out/'workspace').mkdir();(out/'state').mkdir()
    result=restore(source,out/'workspace',out/'state',dict(backup=receipt,retired_results={}),max_bytes=10000,max_files=100)
    assert (out/'state/session.sqlite-wal').read_bytes()==b'WAL'
    assert (out/'workspace/run.sh').stat().st_mode&0o777==0o755
    assert not (out/'state/results/.baseline-cache').exists()
    assert result['task_id']==request['task_id']


def test_restore_never_removes_unproven_pending_result_plan(tmp_path):
    from naiyue_test_agent.cloud_runner.upgrade_backup import backup
    from naiyue_test_agent.cloud_runner.restore_backup import restore
    workspace=tmp_path/'workspace';state=tmp_path/'state';workspace.mkdir();state.mkdir()
    plans=state/'result-plans';plans.mkdir();(plans/(str(uuid4())+'.json')).write_text('{}')
    request=dict(command_id=str(uuid4()),task_id='a'*32,generation=2,reservation_sha256='b'*64)
    source=tmp_path/'backup';receipt=backup(workspace,state,source,request,max_bytes=10000,max_files=100)
    out=tmp_path/'out';out.mkdir();(out/'workspace').mkdir();(out/'state').mkdir()
    with pytest.raises(CloudError,match='restore_result_unconfirmed'):
        restore(source,out/'workspace',out/'state',dict(backup=receipt,retired_results={}),max_bytes=10000,max_files=100)
    assert source.exists()


def test_real_frozen_input_can_capture_again_after_raw_new_root_restore(tmp_path):
    from test_result_capture_cache import setup_capture,next_plan,artifact
    from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result
    from naiyue_test_agent.cloud_runner.upgrade_backup import backup
    from naiyue_test_agent.cloud_runner.restore_backup import restore
    from naiyue_test_agent.contracts.cloud_results import ResultLimits
    import shutil
    workspace,inputs,plan,boundary,limits=setup_capture(tmp_path)
    state=tmp_path/'state';state.mkdir();spools=state/'results'
    freeze_result(workspace,inputs,spools/plan.result_id,plan,boundary,1,ResultLimits(),limits)
    assert (spools/'.baseline-cache'/plan.input_id).is_dir()
    request=dict(command_id=str(uuid4()),task_id=plan.task_id,generation=1,reservation_sha256='b'*64)
    source=tmp_path/'backup';receipt=backup(workspace,state,source,request,max_bytes=10**7,max_files=1000)
    out=tmp_path/'new';out.mkdir();(out/'workspace').mkdir();(out/'state').mkdir()
    restore(source,out/'workspace',out/'state',dict(backup=receipt,
        retired_results={plan.result_id:digest(plan.model_dump(mode='json'))}),max_bytes=10**7,max_files=1000)
    restored_inputs=out/'input';shutil.copytree(inputs,restored_inputs)
    # A restored immutable archive has a new inode, just as the new-root Owner
    # input rebind does. The baseline must be rebuilt from those verified bytes.
    value=json.loads((restored_inputs/'input.json').read_text())
    info=(restored_inputs/'archive').stat()
    value['archive_identity']=[getattr(info,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')]
    (restored_inputs/'input.json').write_text(json.dumps(value))
    (out/'workspace/keep.txt').write_text('after restore\n')
    second=next_plan(plan)
    result=freeze_result(out/'workspace',restored_inputs,out/'state/results'/second.result_id,
        second,boundary,1,ResultLimits(),limits)
    assert b'after restore' in artifact(result,'patch')
    assert (out/'state/results/.baseline-cache'/plan.input_id).is_dir()


def test_restored_attachment_can_be_reused_without_overwriting_agent_changes(tmp_path):
    from test_attachment_install import fixture
    from naiyue_test_agent.cloud_runner.attachment_install import install
    from naiyue_test_agent.cloud_runner.upgrade_backup import backup
    from naiyue_test_agent.cloud_runner.restore_backup import restore
    from naiyue_test_agent.contracts.cloud_inputs import InputLimits
    package,root,state=fixture(tmp_path);aid=str(uuid4());limits=InputLimits()
    relative=install(package.directory,root,state,aid,package.input_id,limits)
    (root/relative/'.env').write_text('agent modified')
    request=dict(command_id=str(uuid4()),task_id='a'*32,generation=1,reservation_sha256='b'*64)
    source=tmp_path/'backup';receipt=backup(root.parent,state,source,request,max_bytes=10**7,max_files=1000)
    out=tmp_path/'new';out.mkdir();(out/'workspace').mkdir();(out/'state').mkdir()
    restore(source,out/'workspace',out/'state',dict(backup=receipt,retired_results={}),max_bytes=10**7,max_files=1000)
    assert install(package.directory,out/'workspace/project',out/'state',aid,package.input_id,limits)==relative
    assert (out/'workspace/project'/relative/'.env').read_text()=='agent modified'
