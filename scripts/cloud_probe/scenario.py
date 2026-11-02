"""Real SDK lifecycle and task-network S00 gate on a Linux Docker host."""
import asyncio
from datetime import timedelta
import hashlib
import json
from importlib.metadata import version
import os
from pathlib import Path
import tempfile
import time
import sys

from scripts.cloud_probe.checks import require,validate_container,validate_identity,validate_network
from scripts.cloud_probe.fixtures import NetworkFixture
from scripts.cloud_probe.runtime import close_owned,command,command_text


async def scenario(args,report):
    import docker
    from opensandbox.config import ConnectionConfig
    from opensandbox.models.execd import RunCommandOpts
    from opensandbox.models.sandboxes import Host,Volume
    from opensandbox.sandbox import Sandbox
    from opensandbox.transport import RetryPolicy

    client=docker.from_env(timeout=args.timeout)
    managed={};fixture=None
    try:
        report['phase']='preflight'
        engine=client.info()
        require(engine['OSType']=='linux','linux_docker_required')
        report['platform']={key:engine[key] for key in ('ServerVersion','Architecture','KernelVersion')}
        report['packages']={name:version(name) for name in ('opensandbox','opensandbox-server','grpcio','protobuf')}
        require(report['packages']['opensandbox']==args.sdk_version,'sdk_version_mismatch')
        require(report['packages']['opensandbox-server']==args.server_version,'server_version_mismatch')
        network=client.networks.get(args.network).attrs
        validate_network(network)
        gateway=network['IPAM']['Config'][0]['Gateway']
        image_id=client.images.get(args.image).id
        require(image_id==args.image_id,'template_image_changed')
        report['image_id']=image_id
        root=Path(args.fixture_root)
        require(root.is_absolute() and root.is_dir() and not root.is_symlink(),'fixture_root_invalid')
        directory=Path(tempfile.mkdtemp(prefix='run-'+report['run_id']+'-',dir=root))
        report['fixture_directory']=str(directory)
        paths={}
        for name in ('a/workspace','a/state','b/workspace','b/state','input'):
            path=directory/name;path.mkdir(parents=True,exist_ok=True)
            os.chown(path,args.uid,args.gid);path.chmod(0o755);paths[name]=path
        (paths['input']/'marker').write_text(report['run_id'])
        (directory/'host-secret').write_text('s00-canary-not-a-real-secret')
        config=ConnectionConfig(domain=args.domain,api_key=os.environ[args.api_key_env],
            use_server_proxy=True,retry_policy=RetryPolicy.disabled(),disable_metrics=True,
            request_timeout=timedelta(seconds=args.timeout))

        def container(sandbox):
            items=client.containers.list(all=True,filters={'label':'opensandbox.io/id='+sandbox.id})
            require(len(items)==1,'sandbox_container_identity_ambiguous')
            item=items[0];item.reload()
            require(item.attrs['Config']['Labels'].get('nyt-s00')==report['run_id'],'container_owner_mismatch')
            return item

        async def create(name):
            started=time.monotonic()
            mounts={'/workspace':(str(paths[name+'/workspace']),True),
                    '/state':(str(paths[name+'/state']),True),'/input':(str(paths['input']),False)}
            sandbox=await Sandbox.create(args.image,connection_config=config,
                timeout=timedelta(seconds=args.ttl),ready_timeout=timedelta(seconds=args.timeout),
                resource={'cpu':str(args.cpu),'memory':args.memory},metadata={'nyt-s00':report['run_id']},
                volumes=[Volume(name=destination.strip('/'),host=Host(path=source),
                    mount_path=destination,read_only=not writable)
                    for destination,(source,writable) in mounts.items()])
            managed[sandbox.id]=sandbox
            actual=container(sandbox).attrs
            validate_container(actual,network=args.network,image_id=image_id,uid=args.uid,mounts=mounts)
            identity=await command(sandbox,{'kind':'identity'},timeout=args.timeout)
            validate_identity(identity,uid=args.uid,gid=args.gid)
            report.setdefault('created',[]).append({'sandbox_id':sandbox.id,
                'ready_seconds':round(time.monotonic()-started,3),'identity':identity,
                'memory_bytes':actual['HostConfig']['Memory'],'pids':actual['HostConfig']['PidsLimit']})
            return sandbox

        async def wait_removed(sandbox_id):
            async with asyncio.timeout(args.timeout):
                while client.containers.list(all=True,filters={'label':'opensandbox.io/id='+sandbox_id}):
                    await asyncio.sleep(.1)

        report['phase']='create'
        a=await create('a');b=await create('b')
        for name,sandbox in (('a',a),('b',b)):
            marker=report['run_id']+name
            seeded=await command(sandbox,{'kind':'seed','marker':marker})
            require(seeded=={'input_readonly':True,'input_value':report['run_id']},'input_mount_not_readonly')
            require(await command(sandbox,{'kind':'read'})=={'/workspace':marker,'/state':marker},'task_data_mismatch')
        boundary=await command(a,{'kind':'boundary','forbidden_paths':[
            str(paths['b/workspace']/'marker'),str(directory/'host-secret')],
            'socket_paths':['/var/run/docker.sock'],
            'credential_names':[args.api_key_env,'OPEN_SANDBOX_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY']})
        require(boundary=={'readable':[False,False],'socket_connected':[False],'root_key_env':False},
                'task_host_or_peer_exposure')
        report['file_isolation']=True

        report['phase']='peer_network'
        listener=await b.commands.run(command_text({'kind':'listen'}),opts=RunCommandOpts(background=True))
        require(listener.error is None,'peer_listener_start_failed')
        async with asyncio.timeout(args.timeout):
            while True:
                port=(await command(b,{'kind':'port'}))['port']
                if port is not None:break
                await asyncio.sleep(.1)
        require((await command(b,{'kind':'tcp','host':'127.0.0.1','port':port,'timeout':2}))['connected'],
                'peer_listener_positive_control_failed')
        peer_ip=container(b).attrs['NetworkSettings']['Networks'][args.network]['IPAddress']
        require(not (await command(a,{'kind':'tcp','host':peer_ip,'port':port,'timeout':2}))['connected'],
                'peer_network_exposed')
        _,port=args.domain.rsplit(':',1)
        require(not (await command(a,{'kind':'tcp','host':gateway,'port':int(port),'timeout':2}))['connected'],
                'management_port_exposed')
        report['peer_and_management_network_isolation']=True

        report['phase']='http_grpc_relay'
        fixture=NetworkFixture(gateway,report['run_id'])
        payload=dict(kind='network',timeout=2,http_sha256=hashlib.sha256(report['run_id'].encode()).hexdigest(),
            **fixture.endpoints())
        online=await command(a,payload)
        require(online=={'http':True,'grpc_unary':True,'grpc_bidi':True},'relay_positive_failed')
        fixture.disconnect()
        offline=await command(a,payload)
        require(offline=={'http':False,'grpc_unary':False,'grpc_bidi':False},'relay_disconnect_not_observed')
        fixture.reconnect()
        require(fixture.endpoints()=={key:payload[key] for key in ('http_url','grpc_target')},'relay_endpoint_changed')
        reconnected=await command(a,payload)
        require(reconnected==online,'relay_reconnect_failed')
        report['simulated_relay']={'online':online,'disconnected':offline,'reconnected':reconnected,
            'scope':'Linux bridge to loopback fixtures; Windows/real SSH and business RPC pending'}

        if args.external_http_url:
            report['phase']='external_ssh_relay'
            external_payload=dict(kind='network',timeout=2,http_url=args.external_http_url,
                http_sha256=args.external_http_sha256,grpc_target=args.external_grpc_target)
            observed=await command(a,external_payload)
            expected=dict.fromkeys(('http','grpc_unary','grpc_bidi'),args.external_expect=='online')
            report['external_relay']={'expected':args.external_expect,'observed':observed,
                'endpoint_sha256':hashlib.sha256(json.dumps(external_payload,sort_keys=True).encode()).hexdigest()}
            require(observed==expected,'external_relay_expectation_failed')

        report['phase']='renew_pause_resume'
        before=(await a.get_info()).expires_at
        await a.renew(timedelta(seconds=args.ttl*2))
        after=(await a.get_info()).expires_at
        require(before is not None and after is not None and after>before,'lease_not_renewed')
        await a.pause()
        require(container(a).attrs['State']['Paused'] is True,'pause_not_observed')
        resumed=await Sandbox.resume(a.id,connection_config=config,resume_timeout=timedelta(seconds=args.timeout))
        managed[a.id]=resumed;await a.close();a=resumed
        require(await command(a,{'kind':'read'})=={'/workspace':report['run_id']+'a','/state':report['run_id']+'a'},
                'resume_data_lost')
        report['renew_pause_resume']=True

        report['phase']='kill_recreate'
        old_id=a.id
        await close_owned(a,report['run_id']);del managed[old_id]
        await wait_removed(old_id)
        a=await create('a')
        require(a.id!=old_id and await command(a,{'kind':'read'})=={
            '/workspace':report['run_id']+'a','/state':report['run_id']+'a'},'persistent_recreate_failed')
        report['kill_recreate']=True
        report['phase']='completed'
    finally:
        primary=sys.exc_info()[1]
        failures=[]
        if fixture is not None:
            try:fixture.close()
            except Exception as error:failures.append(type(error).__name__)
        for sandbox_id,sandbox in list(managed.items()):
            try:
                await close_owned(sandbox,report['run_id'])
                async with asyncio.timeout(args.timeout):
                    while client.containers.list(all=True,filters={'label':'opensandbox.io/id='+sandbox_id}):
                        await asyncio.sleep(.1)
            except Exception as error:failures.append(type(error).__name__)
        try:
            remaining=client.containers.list(all=True,filters={'label':'nyt-s00='+report['run_id']})
            report['remaining_container_ids']=[item.id for item in remaining]
        except Exception as error:
            failures.append(type(error).__name__);report['remaining_container_ids']=None
        try:client.close()
        except Exception as error:failures.append(type(error).__name__)
        report['cleanup_errors']=failures
        require(primary is not None or (not failures and report['remaining_container_ids']==[]),'owned_cleanup_incomplete')
