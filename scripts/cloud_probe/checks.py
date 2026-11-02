"""Validate observed Docker/kernel facts, never merely requested settings."""


class GateError(RuntimeError):
    """A bounded, non-secret gate code suitable for an evidence report."""


def require(condition,code):
    if not condition:
        raise GateError(code)


def validate_network(facts):
    require(facts.get('Driver')=='bridge','network_not_bridge')
    require(facts.get('Options',{}).get('com.docker.network.bridge.enable_icc')=='false',
            'network_peer_isolation_missing')


def validate_container(facts,*,network,image_id,uid,mounts):
    config=facts.get('Config',{});host=facts.get('HostConfig',{})
    require(facts.get('Image')==image_id,'image_identity_mismatch')
    require(config.get('User','').split(':')[0]==str(uid) and uid>0,'container_user_mismatch')
    require(host.get('NetworkMode')==network,'container_network_mismatch')
    require(host.get('Privileged') is False and not host.get('CapAdd'),'container_privileged')
    require('ALL' in (host.get('CapDrop') or []),'container_capabilities_not_dropped')
    options=host.get('SecurityOpt') or []
    require(any(x in ('no-new-privileges','no-new-privileges=true') for x in options),
            'container_privilege_escalation_allowed')
    require(not any('unconfined' in x for x in options),'container_profile_unconfined')
    require(host.get('PidMode')!='host' and host.get('IpcMode')!='host','container_host_namespace')
    cpu=host.get('NanoCpus') or (host.get('CpuQuota',0) if host.get('CpuPeriod',0)>0 else 0)
    require(host.get('PidsLimit',0)>0 and host.get('Memory',0)>0 and cpu>0,
            'container_resource_limit_missing')
    observed={x['Destination']:(x['Source'],x['RW']) for x in facts.get('Mounts',[])}
    require(len(observed)==len(facts.get('Mounts',[])) and observed==mounts,'container_mount_mismatch')
    names={x.partition('=')[0] for x in config.get('Env',[])}
    require(not names.intersection({'OPEN_SANDBOX_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY'}),
            'root_credential_in_task_environment')


def validate_identity(facts,*,uid,gid):
    require(facts.get('uid')==uid>0 and facts.get('gid')==gid>0,'task_identity_mismatch')
    require(isinstance(facts.get('groups'),list) and 0 not in facts['groups'],'task_root_group')
    require(facts.get('no_new_privs')==1,'task_new_privileges')
    require(facts.get('caps')==dict.fromkeys(('CapEff','CapPrm','CapInh','CapAmb','CapBnd'),0),
            'task_capabilities_present')
