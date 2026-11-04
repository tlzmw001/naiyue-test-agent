"""Private durable update requests and process locks, independent of Coordinator."""
from contextlib import contextmanager
import errno,json,logging,os,time
from pathlib import Path
from uuid import UUID

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import (open_store_file,open_store_readonly,check_store_directory,
    acquire_store_lock,release_store_lock,sync_store_directory)
from naiyue_test_agent.storage.cloud.schema import private_file
from naiyue_test_agent.application.cloud.ownership import private_directory

logger=logging.getLogger(__name__)


def directory(path):
    path=Path(path)
    if not path.exists():
        if not path.parent.exists():directory(path.parent)
        check_store_directory(path.parent);path.mkdir(mode=0o700)
    private_directory(path)
    return path


def write(path,value):
    check_store_directory(path.parent)
    temporary=path.with_name(path.name+'.next')
    fd=open_store_file(temporary)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        json.dump(value,stream,allow_nan=False,sort_keys=True,ensure_ascii=False,separators=(',',':'));stream.truncate();stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path);sync_store_directory(path.parent)


def read(path,limit=1024*1024):
    for attempt in range(3):
        private_file(path)
        try:
            with os.fdopen(open_store_readonly(path),'rb') as f:raw=f.read(limit+1)
            break
        except OSError as error:
            # A writer may atomically replace this record between open and lstat.
            # Retry the complete validation; never accept the rejected handle.
            if attempt==2 or error.errno!=errno.EINVAL or error.strerror!='unsafe_store_file':raise
            logger.warning('update_record_read_retry attempt=%s',attempt+1)
    if len(raw)>limit:raise CloudError('update_record_invalid')
    try:
        from naiyue_test_agent.contracts.cloud_json import parse_object
        value=parse_object(raw,byte_limit=limit,depth_limit=32)
        if not isinstance(value,dict):raise ValueError()
        return value
    except (CloudError,ValueError,TypeError):raise CloudError('update_record_invalid') from None


class UpdateStore:
    def __init__(self,settings):
        self.settings=settings
        self.root=directory(settings.work_dir);self.jobs=directory(self.root/'jobs')
        self.target=dict(platform=settings.platform,template_id=settings.build.template_id,backend_id=settings.build.backend_id,
            uid=settings.build.uid,gid=settings.build.gid,task_data_dir=str(settings.task_data_dir) if settings.task_data_dir else None)

    def require_target(self,value):
        if value.get('target')!=self.target:raise CloudError('command_id_conflict')
        if value.get('operation') and value.get('operation_target')!=self.operation_target:raise CloudError('command_id_conflict')

    @property
    def operation_target(self):
        return dict(cloud_settings_file=str(self.settings.cloud_settings_file) if self.settings.cloud_settings_file else None,
            recovery_dir=str(self.settings.recovery_dir) if self.settings.recovery_dir else None)

    def path(self,ident):
        try:
            if str(UUID(ident))!=ident:raise ValueError()
        except (CloudError,ValueError,TypeError):raise CloudError('invalid_update_command',422) from None
        return self.jobs/(ident+'.json')

    @contextmanager
    def lock(self,name):
        if name!='build':self.path(name)
        fd=open_store_file(self.jobs/(name+'.lock'))
        try:
            try:acquire_store_lock(fd)
            except BlockingIOError:raise CloudError('update_busy') from None
            try:yield
            finally:release_store_lock(fd)
        finally:os.close(fd)

    def create(self,ident,release_id,*,task_id=None,activate=True,manifest_sha256=None,instance_id=None,expected_revision=None):
        from pydantic import TypeAdapter
        # Apply exactly the manifest identity grammar before constructing any path.
        import re
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',release_id):raise CloudError('invalid_release_id',422)
        if task_id is not None:
            from naiyue_test_agent.contracts.cloud_tasks import TaskId
            TypeAdapter(TaskId).validate_python(task_id)
        if type(activate) is not bool:raise CloudError('invalid_update_command',422)
        pins={k:v for k,v in dict(manifest_sha256=manifest_sha256,instance_id=instance_id,expected_revision=expected_revision).items() if v is not None}
        if manifest_sha256 is not None:
            from naiyue_test_agent.contracts.cloud_tasks import ContentId
            TypeAdapter(ContentId).validate_python(manifest_sha256)
        try:old=self.read(ident)
        except FileNotFoundError:pass
        else:
            if old['release_id']!=release_id or old.get('task_id')!=task_id or old.get('target')!=self.target or old.get('activate',True)!=activate or any(old.get(k)!=v for k,v in pins.items()):raise CloudError('command_id_conflict')
            return old
        with self.lock(ident):
            try:old=self.read(ident)
            except FileNotFoundError:pass
            else:
                if old['release_id']!=release_id or old.get('task_id')!=task_id or old.get('target')!=self.target or old.get('activate',True)!=activate or any(old.get(k)!=v for k,v in pins.items()):raise CloudError('command_id_conflict')
                return old
            value=dict(command_id=ident,release_id=release_id,task_id=task_id,activate=activate,phase='queued',step='queued',target=self.target,created_at=time.time(),updated_at=time.time(),error_code=None)|pins
            with self.lock('build'):
                self._available();write(self.path(ident),value)
            return value

    def create_operation(self,ident,operation,request):
        if operation not in ('backup','restore','gc'):raise CloudError('invalid_update_command')
        with self.lock(ident):
            try:old=self.read(ident)
            except FileNotFoundError:pass
            else:
                self.require_target(old)
                if old.get('operation')!=operation or old.get('request')!=request:raise CloudError('command_id_conflict')
                return old
            now=time.time();value=dict(command_id=ident,operation=operation,request=request,target=self.target,
                operation_target=self.operation_target,phase='queued',step='queued',created_at=now,updated_at=now,error_code=None)
            with self.lock('build'):
                self._available();write(self.path(ident),value)
            return value

    def _available(self):
        try:value=read(self.root/'gc-guard.json')
        except FileNotFoundError:return
        if value.get('phase')!='completed':raise CloudError('update_gc_pending')

    def read(self,ident):
        value=read(self.path(ident))
        if value.get('command_id')!=ident:raise CloudError('update_record_invalid')
        return value

    def save(self,ident,**changes):
        value=self.read(ident)|changes|dict(updated_at=time.time())
        write(self.path(ident),value);return value
