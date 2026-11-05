"""One explicit durable update producer; no terminal or Coordinator process owns it."""
import argparse,json,logging,os,subprocess,sys
from uuid import UUID,uuid5

from pydantic import ValidationError
from naiyue_test_agent.contracts.cloud_tasks import CloudError,TemplateReceipt
from naiyue_test_agent.cloud_files.configuration import read_settings
from naiyue_test_agent.cloud_files.client import CloudClient,CloudConnection
from .settings import UpdateSettings
from .connection import update_connection
from naiyue_test_agent.contracts.cloud_versions import OWNER_PROTOCOL,RUNNER_PROTOCOL,STATE_SCHEMA
from .source import load_release,artifact_file,ensure_capacity
from .store import UpdateStore,directory,write,read
from .template import prepare_template,TemplateBuildSettings,build_fingerprint

logger=logging.getLogger(__name__)


def check_update(settings,release_id,client):
    manifest,sha=load_release(settings,release_id)
    return _plan(settings,manifest,sha,client)


def _plan(settings,manifest,sha,client):
    release_id=manifest.release_id
    instance=client.request('GET','/instance')
    if instance.get('instance_id')!=client.settings.instance_id:raise CloudError('cloud_instance_mismatch')
    issues=[]
    from packaging.version import Version
    if manifest.min_coordinator_version is not None and Version(instance.get('version','0'))<Version(manifest.min_coordinator_version):
        issues.append('coordinator_update_required')
    if instance.get('protocol')!=manifest.coordinator_protocol or manifest.coordinator_protocol!=OWNER_PROTOCOL:issues.append('coordinator_protocol')
    if manifest.runner_protocol!=RUNNER_PROTOCOL or manifest.state_schema!=STATE_SCHEMA or manifest.min_connector_protocol>OWNER_PROTOCOL:issues.append('runner_protocol_or_schema')
    if 'releases:write' not in instance.get('capabilities',[]):issues.append('coordinator_update_required')
    if set(manifest.required_capabilities)-set(instance.get('capabilities',[])):issues.append('missing_capabilities')
    if settings.platform not in manifest.wheels:issues.append('missing_platform')
    active=client.request('GET','/releases/active/'+settings.build.template_id) if 'releases:write' in instance.get('capabilities',[]) else None
    return dict(release_id=release_id,manifest_sha256=sha,compatible=not issues,issues=issues,active=active,instance_id=instance.get('instance_id'),coordinator_version=instance.get('version'),
        target_version=manifest.agent_version,maintenance_required='coordinator_update_required' in issues,
        coordinator_wheel=manifest.wheels[settings.platform].model_dump() if settings.platform in manifest.wheels else None)


def execute(settings,ident,*,client=None):
    store=UpdateStore(settings);owned=client is None
    with store.lock(ident):
        value=store.read(ident)
        store.require_target(value)
        if value['phase'] in ('completed','prepared'):return value
        try:
            if value.get('operation'):
                from .operations import execute_operation
                return execute_operation(settings,ident,store)
            with store.lock('build'):
                if owned:client=CloudClient(update_connection(settings))
                if value.get('task_id'):
                    from .upgrade import execute_upgrade
                    return execute_upgrade(settings,ident,store,client)
                store.save(ident,phase='running',step='verifying',error_code=None)
                manifest,sha=load_release(settings,value['release_id'],expected_sha256=value.get('manifest_sha256'))
                store.save(ident,manifest_sha256=sha)
                plan=_plan(settings,manifest,sha,client)
                if value.get('instance_id') is not None and value['instance_id']!=plan['instance_id']:raise CloudError('cloud_instance_mismatch')
                expected=value.get('expected_revision',plan['active']['revision'] if plan['active'] is not None else None)
                if expected is not None:store.save(ident,expected_revision=expected,instance_id=plan['instance_id'])
                if not plan['compatible']:raise CloudError('release_protocol_incompatible')
                wheel=artifact_file(settings,manifest.wheels[settings.platform]);kit=artifact_file(settings,manifest.kit);lock=artifact_file(settings,manifest.test_lock)
                job=directory(settings.work_dir/ident)
                profile=job/'toolchain.json';write(profile,dict(schema_version=1,aitest_kit=dict(version=manifest.kit_version,wheel_sha256=manifest.kit.sha256)))
                build=TemplateBuildSettings.model_validate(settings.build.model_dump()|dict(wheel=wheel,kit_wheel=kit,kit_profile=profile,
                    test_lock=lock,wheel_sha256=manifest.wheels[settings.platform].sha256,base_image=manifest.base_image,
                    image_tag=settings.build.image_tag+'-'+ident.replace('-',''),output=job/'receipt.json',
                    expected_runner_version=manifest.agent_version,expected_runner_protocol=manifest.runner_protocol,
                    expected_state_schema=manifest.state_schema,resume_existing=True))
                store.save(ident,step='building')
                ensure_capacity(settings,0)
                receipt=prepare_template(build,manifest.release_id)
                if (receipt.runner_release_id!=manifest.release_id or receipt.wheel_sha256!=manifest.wheels[settings.platform].sha256
                        or receipt.kit_wheel_sha256!=manifest.kit.sha256):raise CloudError('release_template_mismatch')
                proof=build_fingerprint(release_id=manifest.release_id,wheel_sha256=receipt.wheel_sha256,
                    kit=dict(version=manifest.kit_version,wheel_sha256=manifest.kit.sha256),test_lock_sha256=manifest.test_lock.sha256,
                    base_image=manifest.base_image,uid=receipt.uid,gid=receipt.gid,backend_id=receipt.backend_id)
                store.save(ident,step='registering',image_id=receipt.image_id,build_input_sha256=proof)
                client.request('POST','/releases/register',json=dict(command_id=str(uuid5(UUID(ident),'register')),
                    release=manifest.model_dump(),template=receipt.model_dump()))
                if not value.get('activate',True):return store.save(ident,phase='completed',step='ready',error_code=None)
                store.save(ident,step='activating')
                client.request('POST','/releases/activate',json=dict(command_id=str(uuid5(UUID(ident),'activate')),
                    release_id=manifest.release_id,template_id=receipt.template_id,expected_revision=expected))
                return store.save(ident,phase='completed',step='completed',error_code=None)
        except (CloudError,OSError,ValueError,ValidationError) as error:
            code=error.code if isinstance(error,CloudError) else 'update_local_failure'
            logger.error('cloud_update_failed command_id=%s code=%s error_type=%s',ident,code,type(error).__name__)
            return store.save(ident,phase='unknown' if code=='upgrade_backup_unknown' else 'blocked' if code=='update_busy' else 'failed',error_code=code)
        finally:
            if owned and client is not None:client.close()


def launch(settings_file,ident):
    settings=read_settings(settings_file,UpdateSettings);store=UpdateStore(settings)
    value=store.read(ident);store.require_target(value)
    if value['phase'] in ('completed','prepared'):return value
    try:
        with store.lock(ident):pass
    except CloudError as error:
        if error.code!='update_busy':raise
        return store.read(ident)
    # A worker's per-command and global build locks arbitrate launch/resume races.
    from naiyue_test_agent.storage.files import open_store_file
    log=open_store_file(settings.work_dir/'worker.log')
    if os.name!='nt':
        import fcntl
        fcntl.fcntl(log,fcntl.F_SETFL,fcntl.fcntl(log,fcntl.F_GETFL)|os.O_APPEND)
    try:
        subprocess.Popen([sys.executable,'-I','-m','naiyue_test_agent.cloud_install.worker','--settings',str(settings_file),'--command-id',ident],
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True,start_new_session=True)
    except OSError as error:
        logger.error('cloud_update_spawn_failed error_type=%s',type(error).__name__)
        with store.lock(ident):store.save(ident,phase='failed',error_code='update_spawn_failed')
        raise CloudError('update_spawn_failed',503) from None
    finally:os.close(log)
    return value


def main():
    p=argparse.ArgumentParser();p.add_argument('--settings',required=True);p.add_argument('--command-id',required=True)
    args=p.parse_args();logging.basicConfig(level=logging.INFO)
    for name in ('httpx','httpcore','httpx2','httpcore2'):logging.getLogger(name).setLevel(logging.ERROR)
    try:
        result=execute(read_settings(args.settings,UpdateSettings),args.command_id)
        return 0 if result['phase'] in ('completed','prepared') else 1
    except (CloudError,OSError,ValueError) as error:
        logger.error('cloud_update_worker_unavailable error_type=%s',type(error).__name__);return 1


if __name__=='__main__':raise SystemExit(main())
