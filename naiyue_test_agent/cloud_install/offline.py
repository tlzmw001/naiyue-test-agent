"""Exclusive operator ownership is required in addition to physical writer absence."""
from contextlib import contextmanager,ExitStack
from types import SimpleNamespace
from pathlib import Path
import json,os

from naiyue_test_agent.application.cloud.settings import CloudSettings
from naiyue_test_agent.application.cloud.ownership import CloudOwner
from naiyue_test_agent.cloud_files.configuration import read_settings
from naiyue_test_agent.cloud_files.client import CloudConnection
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.database import Database
from naiyue_test_agent.storage.cloud.operations import OperationRepository
from naiyue_test_agent.storage.cloud.releases import ReleaseRepository
from naiyue_test_agent.storage.cloud.guards import check_guards
from naiyue_test_agent.cloud_files.delivery_review import directory_scope
from naiyue_test_agent.application.cloud.resource_disk import open_directory,mount_id
from naiyue_test_agent.contracts.projects import ProjectError
from .store import UpdateStore,read
from .upgrade import _run


def ensure_no_writers(settings,instance_id):
    info=json.loads(_run(settings,['info','--format','{{json .}}']))
    if info.get('ID')!=settings.build.backend_id:raise CloudError('backend_identity_mismatch')
    for label in ('nyt-instance','org.naiyue.upgrade-instance'):
        raw=_run(settings,['container','ls','--all','--no-trunc','--filter','label='+label+'='+instance_id,'--format','{{json .}}'])
        if raw.strip():raise CloudError('offline_writer_present')


def configuration(settings):
    if settings.cloud_settings_file is None or settings.task_data_dir is None:raise CloudError('offline_settings_required')
    cloud=read_settings(settings.cloud_settings_file,CloudSettings)
    if cloud.data_dir!=settings.task_data_dir:raise CloudError('cloud_instance_mismatch')
    connection=read_settings(settings.connection_file,CloudConnection)
    separate_roots(cloud,settings)
    return cloud,connection


def separate_roots(cloud,settings):
    def scope(path):
        path=Path(path).expanduser().absolute();tail=[];parent=path
        while not os.path.lexists(parent):tail.insert(0,parent.name);parent=parent.parent
        try:ancestors=directory_scope(parent)
        except ProjectError:raise CloudError('offline_roots_unsafe') from None
        fd=open_directory(parent)
        try:mount=mount_id(fd);device=os.fstat(fd).st_dev
        finally:os.close(fd)
        return dict(path=path,ancestors=ancestors,tail=tail,mount=mount,device=device)
    def overlaps(a,b):
        if a['path'].is_relative_to(b['path']) or b['path'].is_relative_to(a['path']):return True
        if not a['tail'] and a['ancestors'][0] in b['ancestors']:return True
        if not b['tail'] and b['ancestors'][0] in a['ancestors']:return True
        if a['device']==b['device'] and a['mount']!=b['mount']:raise CloudError('offline_roots_ambiguous_mount')
        if a['tail'] and b['tail'] and a['ancestors'][0]==b['ancestors'][0]:
            left=[v.casefold() for v in a['tail']];right=[v.casefold() for v in b['tail']]
            return left[:len(right)]==right or right[:len(left)]==left
        return False
    paths=[scope(p) for p in [cloud.data_dir,settings.work_dir]+([settings.recovery_dir] if settings.recovery_dir else [])]
    if any(overlaps(a,b) for i,a in enumerate(paths) for b in paths[i+1:]):raise CloudError('offline_roots_overlap')
    protected=[scope(p) for p in cloud.protected_roots]
    if any(overlaps(a,b) for a in paths for b in protected):raise CloudError('cloud_protected_root')


@contextmanager
def offline(settings,*,allow_restore=False,allow_gc=False,quiet=True,maintenance_result=None,additional_root=None):
    cloud,connection=configuration(settings)
    with ExitStack() as stack:
        stack.enter_context(UpdateStore(settings).lock('build'))
        owners={}
        configs=[cloud]
        if additional_root is not None:
            additional_root=Path(additional_root)
            if settings.recovery_dir is None or not additional_root.is_relative_to(settings.recovery_dir) or additional_root==settings.recovery_dir:
                raise CloudError('restore_target_invalid')
            configs.append(cloud.model_copy(update={'data_dir':additional_root}))
        for config in sorted(configs,key=lambda value:str(value.data_dir)):
            owners[config.data_dir]=stack.enter_context(CloudOwner(config))
        owner=owners[cloud.data_dir];check_guards(cloud.data_dir,allow_restore=allow_restore,allow_gc=allow_gc)
        separate_roots(cloud,settings)
        if owner.record['status']!='ready' or owner.record['instance_id']!=connection.instance_id:raise CloudError('cloud_instance_mismatch')
        if maintenance_result is not None:
            if maintenance_result['reservation'].get('instance_id')!=connection.instance_id:raise CloudError('cloud_instance_mismatch')
            from .upgrade import reconcile_backup
            reconcile_backup(settings,maintenance_result)
        ensure_no_writers(settings,connection.instance_id)
        database=Database.open_cloud(cloud.data_dir/'cloud.sqlite',profile='registry',initialize=False);stack.callback(database.close)
        ReleaseRepository(database,cloud.max_receipts).retire_process_io()
        repository=OperationRepository(database);snapshot=repository.snapshot(quiet=quiet)
        yield SimpleNamespace(settings=cloud,owner=owner,instance_id=connection.instance_id,database=database,repository=repository,snapshot=snapshot)
