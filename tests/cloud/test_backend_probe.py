"""S00 policy rejects unsafe real-container facts; no Docker needed here."""
from copy import deepcopy

import pytest


def container_facts():
    return {
        'Image':'sha256:fixture',
        'Config':{'User':'1000:1000','Env':['PATH=/usr/bin']},
        'HostConfig':{'NetworkMode':'s00-net','Privileged':False,
            'CapAdd':None,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges=true'],
            'PidMode':'','IpcMode':'private','PidsLimit':256,'Memory':2147483648,
            'NanoCpus':2000000000},
        'Mounts':[
            {'Source':'/fixtures/a/workspace','Destination':'/workspace','RW':True},
            {'Source':'/fixtures/a/state','Destination':'/state','RW':True},
            {'Source':'/fixtures/input','Destination':'/input','RW':False},
        ],
    }


def validate(facts):
    from scripts.cloud_probe.checks import validate_container
    return validate_container(facts,network='s00-net',image_id='sha256:fixture',uid=1000,
        mounts={'/workspace':('/fixtures/a/workspace',True),
                '/state':('/fixtures/a/state',True),'/input':('/fixtures/input',False)})


def test_expected_policy_accepts_only_own_mounts_and_pinned_image():
    assert validate(container_facts()) is None


@pytest.mark.parametrize('section,key,value',[
    ('HostConfig','NetworkMode','host'),('HostConfig','Privileged',True),
    ('HostConfig','CapAdd',['SYS_ADMIN']),('HostConfig','CapDrop',[]),
    ('HostConfig','SecurityOpt',[]),
    ('HostConfig','SecurityOpt',['no-new-privileges=true','seccomp=unconfined']),
    ('HostConfig','PidMode','host'),('HostConfig','IpcMode','host'),
    ('HostConfig','PidsLimit',0),('HostConfig','Memory',0),
    ('HostConfig','NanoCpus',0),('Config','User','root'),
])
def test_unsafe_actual_profile_rejected(section,key,value):
    from scripts.cloud_probe.checks import GateError
    facts=container_facts();facts[section][key]=value
    with pytest.raises(GateError):validate(facts)


@pytest.mark.parametrize('mutation',('foreign','input_rw','source_mismatch','missing','image'))
def test_mount_and_image_substitution_rejected(mutation):
    from scripts.cloud_probe.checks import GateError
    facts=container_facts()
    if mutation=='foreign':facts['Mounts'].append({'Source':'/var/run/docker.sock','Destination':'/sock','RW':False})
    elif mutation=='input_rw':facts['Mounts'][-1]['RW']=True
    elif mutation=='source_mismatch':facts['Mounts'][0]['Source']='/fixtures/b/workspace'
    elif mutation=='missing':facts['Mounts'].pop()
    else:facts['Image']='sha256:other'
    with pytest.raises(GateError):validate(facts)


def test_identity_checks_bounding_caps_and_supplementary_root_group():
    from scripts.cloud_probe.checks import GateError,validate_identity
    good={'uid':1000,'gid':1000,'groups':[1000],'no_new_privs':1,
          'caps':dict.fromkeys(('CapEff','CapPrm','CapInh','CapAmb','CapBnd'),0)}
    validate_identity(good,uid=1000,gid=1000)
    for key,value in [('caps',dict(good['caps'],CapBnd=1)),('groups',[0]),('uid',0),('no_new_privs',0)]:
        bad=deepcopy(good);bad[key]=value
        with pytest.raises(GateError):validate_identity(bad,uid=1000,gid=1000)


@pytest.mark.parametrize('network',[
    {'Driver':'host','Options':{}},
    {'Driver':'bridge','Options':{}},
    {'Driver':'bridge','Options':{'com.docker.network.bridge.enable_icc':'true'}},
])
def test_bridge_requires_explicit_peer_isolation(network):
    from scripts.cloud_probe.checks import GateError,validate_network
    with pytest.raises(GateError):validate_network(network)


def test_isolated_bridge_accepted():
    from scripts.cloud_probe.checks import validate_network
    validate_network({'Driver':'bridge','Options':{'com.docker.network.bridge.enable_icc':'false'}})
