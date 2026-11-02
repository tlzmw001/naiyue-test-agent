"""Private, bounded identity markers; no workspace imports or automatic DB recreation."""
import json
import os

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_json import parse_object
from naiyue_test_agent.storage.files import open_store_file,open_store_readonly,sync_store_directory
from naiyue_test_agent.storage.cloud.schema import private_file


def read_json(path,limit=65536):
    private_file(path)
    with os.fdopen(open_store_readonly(path),'rb') as f:raw=f.read(limit+1)
    if len(raw)>limit:raise CloudError('runner_state_invalid')
    return parse_object(raw,byte_limit=limit,depth_limit=32)


def write_json(path,value):
    temporary=path.with_name(path.name+'.next')
    fd=open_store_file(temporary)
    with os.fdopen(fd,'w') as f:
        f.write(json.dumps(value,ensure_ascii=False,allow_nan=False));f.truncate();f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)
    sync_store_directory(path.parent)


def prepare_state(bootstrap,state):
    from naiyue_test_agent.application.cloud.ownership import private_directory
    private_directory(state)
    identity={k:getattr(bootstrap,k) for k in ('task_id','project_id','input_id','instance_id','state_epoch')}
    marker=state/'runner.json';database=state/'session.sqlite'
    try:value=read_json(marker)
    except FileNotFoundError:
        if database.exists() or database.is_symlink():raise CloudError('runner_state_identity_missing')
        write_json(marker,identity|{'status':'initializing'})
        return True
    if value not in (identity|{'status':'initializing'},identity|{'status':'ready'}):
        raise CloudError('runner_state_identity_mismatch')
    if value['status']=='ready' and not database.is_file():raise CloudError('runner_state_missing')
    return value['status']=='initializing'


def ready_state(bootstrap,state):
    identity={k:getattr(bootstrap,k) for k in ('task_id','project_id','input_id','instance_id','state_epoch')}
    write_json(state/'runner.json',identity|{'status':'ready'})
