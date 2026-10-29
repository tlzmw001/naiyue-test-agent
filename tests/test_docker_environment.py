"""Provider rejection happens before borrowing any host or creating replacements."""
import pytest
from naiyue_test_agent.contracts.sandbox import SandboxError,SandboxRecord
from naiyue_test_agent.sandbox_settings import SandboxSettings


def record():
    return SandboxRecord('B','P','S','running','D','nyt-e-b','nyt-v-b','sha256:image',
        SandboxSettings(image='app:test').model_dump(mode='json'),container_id='c'*64)


@pytest.mark.parametrize('field,value', [('ReadonlyRootfs',False),('Privileged',True),('NetworkMode','host'),
    ('CapAdd',['SYS_ADMIN']),('CapAdd',[]),('SecurityOpt',['no-new-privileges','seccomp=unconfined']),
    ('PidMode','host'),('PidMode','container:other')])
@pytest.mark.parametrize('ssh', [False, True])
@pytest.mark.parametrize('cap_prefix',['','CAP_'])
def test_policy_mismatch_cannot_become_success(field,value,ssh,cap_prefix):
    from naiyue_test_agent.execution.docker_environment import verify_container
    row=record()
    container_policy=__import__('json').dumps(row.settings,sort_keys=True)
    if ssh:row=row.changed(settings={**row.settings,'ssh':{'host':'private-control-host'}})
    data={'Id':row.container_id,'Image':row.image_id,'Name':'/'+row.container_name,
        'Config':{'User':'0:0','Labels':{'naiyue.binding':'B','naiyue.project':'P','naiyue.session':'S'},
            'Env':['NYT_ENVIRONMENT='+__import__('json').dumps(row.environment.public(),sort_keys=True),'NYT_SESSION_ID=S',
                   'NYT_SANDBOX_POLICY='+container_policy],
            'Entrypoint':['python'],'Cmd':['-I','-m','naiyue_test_agent.envd','serve','--max-bytes','16777216']},
        'HostConfig':{'ReadonlyRootfs':True,'Privileged':False,'NetworkMode':'bridge',
            'NanoCpus':2000000000,'Memory':2147483648,'PidsLimit':256,
            'CapDrop':['ALL'],'CapAdd':[cap_prefix+x for x in ['SETUID','SETGID','KILL','DAC_OVERRIDE','CHOWN','FOWNER']],
            'SecurityOpt':['no-new-privileges'], 'Tmpfs':{'/tmp':'rw,nosuid,nodev,size=67108864,uid=0,gid=0,mode=1777'}},
        'Mounts':[{'Type':'volume','Name':row.volume_name,'Destination':'/work','RW':True},
                  {'Type':'volume','Name':'nyt-s-'+row.binding_id,'Destination':'/state','RW':True}]}
    verify_container(row,data)
    data['HostConfig'][field]=value
    with pytest.raises(SandboxError,match='env_policy_mismatch'):
        verify_container(row,data)


def test_extra_host_mount_is_rejected():
    from naiyue_test_agent.execution.docker_environment import verify_container
    with pytest.raises(SandboxError):verify_container(record(),{'Mounts':[{'Type':'bind','Source':'/','Destination':'/host'}]})


def test_socket_parent_is_not_owned_by_workload():
    from naiyue_test_agent.execution.docker_environment import TMPFS
    assert 'uid=0,gid=0,mode=1777' in TMPFS


def test_destroy_identity_is_separate_from_broken_execution_policy():
    from naiyue_test_agent.execution.docker_environment import verify_container_identity
    row=record()
    data={'Id':row.container_id,'Image':row.image_id,'Name':'/'+row.container_name,
          'Config':{'Labels':{'naiyue.binding':'B','naiyue.project':'P','naiyue.session':'S'}}}
    verify_container_identity(row,data)
    data['Id']='another-container'
    with pytest.raises(SandboxError,match='env_binding_mismatch'):verify_container_identity(row,data)
