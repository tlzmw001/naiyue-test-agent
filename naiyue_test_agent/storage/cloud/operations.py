"""Offline owned registry facts and consistent snapshots; never task SQLite."""
import hashlib,json,os,sqlite3,time
from pathlib import Path
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CloudTask
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_inputs import InputPackage
from naiyue_test_agent.contracts.cloud_artifacts import ArtifactTransfer,ArtifactRecord
from naiyue_test_agent.storage.files import open_store_file,open_store_readonly,check_store_directory,sync_store_directory
from .releases import meta,put
from .maintenance import blockers


def backup_database(database,destination,*,guard_format=False):
    check_store_directory(destination.parent)
    fd=open_store_file(destination,exclusive_create=True);os.close(fd)
    with database.lock:
        target=sqlite3.connect(destination)
        try:
            database.connection.backup(target)
            if guard_format:target.execute('PRAGMA user_version=2')
            target.commit()
        finally:target.close()
    with os.fdopen(open_store_file(destination),'r+b') as stream:os.fsync(stream.fileno())
    sync_store_directory(destination.parent)


def content_identity(path,sha256,size):
    check_store_directory(path.parent);sha=hashlib.sha256();amount=0
    with os.fdopen(open_store_readonly(path),'rb') as stream:
        before=os.fstat(stream.fileno())
        while data:=stream.read(256*1024):
            amount+=len(data)
            if amount>size:raise CloudError('restore_content_mismatch')
            sha.update(data)
        after=os.fstat(stream.fileno())
        fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
        identity=[getattr(before,k) for k in fields]
        if identity!=[getattr(after,k) for k in fields] or (amount,sha.hexdigest())!=(size,sha256):raise CloudError('restore_content_mismatch')
        return identity


class OperationRepository:
    def __init__(self,database):self.database=database

    def enable_guards(self):
        with self.database.transaction(write=True) as db:db.execute('PRAGMA user_version=2')

    def snapshot(self,*,quiet=True):
        with self.database.transaction() as db:
            if quiet:
                if not meta(db,'maintenance',False):raise CloudError('offline_maintenance_required')
                if blockers(db):raise CloudError('offline_not_quiet')
            tasks=[CloudTask.model_validate_json(r[0]) for r in db.execute('SELECT record_json FROM tasks ORDER BY task_id')]
            if quiet and any(t.lifecycle not in ('stopped','creating','deleted') or t.sandbox_id or t.environment_phase not in ('absent','unconfigured') for t in tasks):
                raise CloudError('offline_requires_stopped_tasks')
            values={}
            for key,raw in db.execute("SELECT key,value FROM meta WHERE key!='instance_id'"):
                if key.startswith('history:'):
                    if raw not in ('initializing','ready'):raise CloudError('cloud_invalid_format',503)
                    values[key]=raw
                else:values[key]=json.loads(raw)
            results=[]
            from naiyue_test_agent.contracts.cloud_results import ResultRecord
            for row in db.execute("SELECT record_json FROM commands WHERE json_extract(record_json,'$.operation')='result:capture'"):
                value=json.loads(row[0])
                if value['result'].get('delete_command_id'):continue
                record=ResultRecord.model_validate(value['result'])
                results.append(dict(task_id=record.plan.task_id,result_id=record.plan.result_id,phase=record.phase,plan_sha256=digest(record.plan.model_dump(mode='json'))))
            upgrades=[]
            for row in db.execute("SELECT command_id,record_json FROM commands WHERE json_extract(record_json,'$.operation')='release:upgrade'"):
                value=json.loads(row[1]);upgrades.append(dict(command_id=row[0],result=value['result']))
            return dict(tasks=[t.model_dump(mode='json') for t in tasks],meta=values,results=results,upgrades=upgrades)

    def remove_releases(self,keys):
        with self.database.transaction(write=True) as db:
            changed=False
            for key in keys:
                if not key.startswith('release:'):raise CloudError('gc_invalid_release')
                changed=bool(db.execute('DELETE FROM meta WHERE key=?',(key,)).rowcount) or changed
                put(db,'release-retired:'+key.removeprefix('release:'),True)
            if changed:put(db,'release-inventory-revision',meta(db,'release-inventory-revision',0)+1)

    def rebind_restored(self,root,*,retained_releases=None):
        snapshot=self.snapshot();changes={}
        for key,value in snapshot['meta'].items():
            if key.startswith('input:'):
                package=InputPackage.model_validate(value);directory=root/'inputs'/package.input_id
                from naiyue_test_agent.cloud_files.archive import strict_json
                with os.fdopen(open_store_readonly(directory/'input.json'),'rb') as stream:
                    disk=InputPackage.model_validate(strict_json(stream.read(16*1024*1024+1)))
                if disk.model_dump(exclude={'archive_identity'})!=package.model_dump(exclude={'archive_identity'}):raise CloudError('restore_content_mismatch')
                identity=content_identity(directory/'archive',package.archive_sha256,package.size)
                for name,expected in (('manifest.json',package.manifest.canonical),('selection.json',package.selection.canonical)):
                    content_identity(directory/name,hashlib.sha256(expected).hexdigest(),len(expected))
                package=package.model_copy(update={'archive_identity':identity});changes[key]=package.model_dump(mode='json',by_alias=True)
                target=directory/'input.json';temporary=directory/'input.restore-next';fd=open_store_file(temporary)
                with os.fdopen(fd,'w') as stream:
                    stream.write(package.model_dump_json(by_alias=True));stream.truncate();stream.flush();os.fsync(stream.fileno())
                os.replace(temporary,target);sync_store_directory(directory)
            elif key.startswith('artifact:'):
                transfer=ArtifactTransfer.model_validate(value)
                if transfer.state!='available':raise CloudError('restore_artifact_incomplete')
                artifact=transfer.artifact;directory=root/'artifacts'/artifact.task_id/artifact.artifact_id
                with os.fdopen(open_store_readonly(directory/'artifact.json'),'rb') as stream:
                    if ArtifactRecord.model_validate_json(stream.read(65537))!=artifact:raise CloudError('restore_content_mismatch')
                identity=content_identity(directory/'bytes',artifact.sha256,artifact.size)
                changes[key]=transfer.model_copy(update={'blob_identity':identity}).model_dump(mode='json')
        with self.database.transaction(write=True) as db:
            put(db,'release-inventory-revision',meta(db,'release-inventory-revision',0)+1)
            if retained_releases is not None:
                for key in snapshot['meta']:
                    if key.startswith('release:') and key not in retained_releases:
                        db.execute('DELETE FROM meta WHERE key=?',(key,))
                        put(db,'release-retired:'+key.removeprefix('release:'),True)
            for key,value in changes.items():put(db,key,value)
            db.execute('DELETE FROM task_credentials')
            db.execute("DELETE FROM meta WHERE key LIKE 'maintenance-lease:%'")
            for value in snapshot['tasks']:
                task=CloudTask.model_validate(value)
                if task.lifecycle=='deleted':continue
                updated=task.model_copy(update=dict(runner_boot_id=None,runner_connection_id=None,runner_phase='unconfigured',
                    runner_model_ids=[],runner_capabilities=[],model_profile_id=None,revision=task.revision+1,updated_at=time.time()))
                db.execute('UPDATE tasks SET revision=?,record_json=? WHERE task_id=?',(updated.revision,updated.model_dump_json(),task.task_id))
