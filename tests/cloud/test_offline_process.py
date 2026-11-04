"""Installed wheel launches an offline operation with no Owner HTTP process."""
import json,subprocess,sys
from uuid import uuid4
from test_update_process import installed_python,until
from test_update_installer import inputs
from lifecycle_helpers import config
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.cloud_install.store import UpdateStore


def test_installed_detached_backup_completes_without_owner_and_original_id_is_terminal(tmp_path):
    python=installed_python(tmp_path);settings,_=inputs(tmp_path);cloud=config(tmp_path)
    with open_cloud_runtime(cloud) as r:
        r.releases.maintenance(dict(command_id=str(uuid4()),action='enter'));instance=r.instance_id
    docker=tmp_path/'controlled-docker'
    docker.write_text(f'#!{sys.executable}\nimport json,sys\nargs=sys.argv[1:]\n'
        f'print(json.dumps(dict(ID={settings.build.backend_id!r})) if "info" in args else "")\n')
    docker.chmod(0o700)
    cloud_file=tmp_path/'cloud.json';cloud_file.write_text(cloud.model_dump_json());cloud_file.chmod(0o600)
    settings.connection_file.write_text(json.dumps(dict(endpoint=cloud.origin,owner_authority=cloud.owner_listen,
        owner_token='test-only-no-live-owner',instance_id=instance)));settings.connection_file.chmod(0o600)
    settings=settings.model_copy(update=dict(cloud_settings_file=cloud_file,task_data_dir=cloud.data_dir,
        recovery_dir=tmp_path/'recovery',build=settings.build.model_copy(update={'docker_executable':str(docker)})))
    path=tmp_path/'update.json';path.write_text(settings.model_dump_json());path.chmod(0o600)
    ident=str(uuid4());base=[str(python),'-I','-m','naiyue_test_agent.cli','cloud','release']
    launched=subprocess.run([*base,'backup','--command-id',ident,'--settings',str(path)],capture_output=True,text=True,timeout=10)
    assert launched.returncode==0,launched.stderr
    store=UpdateStore(settings)
    final=until(lambda:store.read(ident) if store.read(ident)['phase'] in ('completed','failed','unknown') else None)
    assert final['phase']=='completed',final
    for action in ('status','resume'):
        result=subprocess.run([*base,action,ident,'--settings',str(path)],capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)==final
    from naiyue_test_agent.cloud_install.backup import verify_backup
    manifest,_=verify_backup(settings,ident)
    assert manifest['instance_id']==instance and manifest['tasks']==[]
