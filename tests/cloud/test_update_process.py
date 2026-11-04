"""Real CLI/HTTP/detached worker lifecycle with a controlled Docker executable."""
import asyncio,json,os,socket,subprocess,sys,threading,time,venv,sysconfig,platform
from pathlib import Path
from uuid import uuid4
from contextlib import contextmanager

import uvicorn
import pytest
from test_update_installer import inputs
from lifecycle_helpers import config
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.cloud_install.store import UpdateStore


@contextmanager
def server(runtime):
    ready=threading.Event();sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen();port=sock.getsockname()[1]
    class Server(uvicorn.Server):
        async def startup(self,sockets=None):
            await super().startup(sockets=sockets);ready.set()
    service=Server(uvicorn.Config(create_cloud_apps(runtime)[0],lifespan='off',log_level='warning',access_log=False))
    thread=threading.Thread(target=lambda:asyncio.run(service.serve(sockets=[sock])),daemon=True);thread.start()
    try:
        assert ready.wait(10),'controlled HTTP server did not start'
        yield port
    finally:
        service.should_exit=True;thread.join(10);sock.close()
        assert not thread.is_alive()


def until(action,*,timeout=20):
    deadline=time.monotonic()+timeout
    while True:
        value=action()
        if value:return value
        if time.monotonic()>=deadline:raise AssertionError('state did not converge')
        time.sleep(.025)


def docker_fixture(tmp_path,uid,gid,protocol=1):
    path=tmp_path/'controlled-docker';state=tmp_path/'images.json';calls=tmp_path/'calls.jsonl';gate=tmp_path/'continue-build';entered=tmp_path/'entered-build'
    source='''import json,sys,time,os
from pathlib import Path
args=sys.argv[1:]
with CALLS.open('a') as f:f.write(json.dumps(args)+'\\n')
images=json.loads(STATE.read_text()) if STATE.exists() else {}
if 'info' in args:value={'ID':'fixture-daemon','Architecture':'aarch64'}
elif 'ls' in args:
 tag=next(v.split('=',1)[1] for v in args if v.startswith('reference='));print(json.dumps({'Id':images[tag]['Id']}) if tag in images else '');sys.exit(0)
elif 'build' in args:
 ENTERED.write_text(json.dumps(dict(pid=os.getpid(),sid=os.getsid(0))))
 while not GATE.exists():time.sleep(.025)
 tag=args[args.index('--tag')+1];label=args[args.index('--label')+1].split('=',1)[1]
 images[tag]={'Id':'sha256:'+'1'*64,'Config':{'Labels':{'org.naiyue.build-input':label}}};STATE.write_text(json.dumps(images))
 Path(args[args.index('--iidfile')+1]).write_text(images[tag]['Id']);value={}
elif 'inspect' in args:value=[images.get(args[-1],{'Id':args[-1]})]
elif 'run' in args:value=dict(schema_version=1,uid=UID,euid=UID,gid=GID,egid=GID,groups=[GID],no_new_privs=1,
 caps=dict.fromkeys(('CapEff','CapPrm','CapInh','CapAmb','CapBnd'),0),runner_readonly=True,runner_version='0.5.6',runner_protocol=PROTOCOL,state_schema=1,kit_version='0.6.0',
 toolchain_sha256='4'*64,checks=dict.fromkeys(('kit_cli','doctor','pytest','report','pip_check','native_tools'),'passed'))
else:raise RuntimeError('unexpected controlled command')
print(json.dumps(value))
'''
    header=f'#!{sys.executable}\nfrom pathlib import Path\nSTATE=Path({str(state)!r});CALLS=Path({str(calls)!r});GATE=Path({str(gate)!r});ENTERED=Path({str(entered)!r});UID={uid};GID={gid};PROTOCOL={protocol}\n'
    path.write_text(header+source);path.chmod(0o700)
    return path,calls,gate,entered


def installed_python(tmp_path):
    # This cross-process test installs the standard product wheel. Only third-party
    # dependencies are shared from the test venv; no source-root PYTHONPATH fallback.
    root=Path(__file__).resolve().parents[2];dist=tmp_path/'dist'
    assets=tmp_path/'worker-test-assets';(assets/'web').mkdir(parents=True);(assets/'licenses').mkdir()
    (assets/'web/index.html').write_text('<div>Controlled worker test; no UI claims.</div>')
    (assets/'web/assets').mkdir();(assets/'web/assets/worker-test.js').write_text('export {};')
    (assets/'web/assets/worker-test.css').write_text('body {}')
    (assets/'licenses/frontend.json').write_text('[]')
    built=subprocess.run([sys.executable,'-m','scripts.build_release','--target','macos-arm64' if sys.platform=='darwin' else ('linux-arm64' if platform.machine() in ('arm64','aarch64') else 'linux-x86_64'),
        '--frontend-assets',str(assets),'--output',str(dist)],cwd=root,capture_output=True,text=True,timeout=120)
    assert built.returncode==0,built.stderr[-4000:]
    env=tmp_path/'installed';venv.EnvBuilder(with_pip=False).create(env)
    python=env/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    result=subprocess.run([str(python),'-c','import sysconfig;print(sysconfig.get_path("purelib"))'],capture_output=True,text=True,check=True)
    site=Path(result.stdout.strip());(site/'test-dependencies.pth').write_text(sysconfig.get_path('purelib')+'\n')
    install=subprocess.run([str(python),'-m','pip','install','--no-deps','--no-index',str(next(dist.glob('*.whl')))],capture_output=True,text=True,timeout=60)
    assert install.returncode==0,install.stderr
    return python


@pytest.mark.parametrize('protocol',[1,2])
def test_detached_update_survives_launcher_exit_and_lost_activation_ack(tmp_path,protocol):
    python=installed_python(tmp_path)
    settings,manifest=inputs(tmp_path);docker,calls,gate,entered=docker_fixture(tmp_path,settings.build.uid,settings.build.gid,protocol)
    settings=settings.model_copy(update={'build':settings.build.model_copy(update={'docker_executable':str(docker)})})
    with open_cloud_runtime(config(tmp_path)) as r:
        original=r.releases.activate;lost=[]
        def lose_once(value):
            result=original(value)
            if not lost:lost.append(True);raise CloudError('fixture_lost_activation_ack',503)
            return result
        r.releases.activate=lose_once
        # Binding uses a preallocated port to preserve Host/Origin authorization.
        with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        r.settings=r.settings.model_copy(update={'owner_listen':f'127.0.0.1:{port}'})
        # server() chooses its own socket; use its port as the same trusted authority.
        with server(r) as actual:
            r.settings=r.settings.model_copy(update={'owner_listen':f'127.0.0.1:{actual}'})
            settings.connection_file.write_text(json.dumps(dict(endpoint=f'http://127.0.0.1:{actual}',owner_authority=f'127.0.0.1:{actual}',
                owner_token=r.settings.owner_token.get_secret_value(),instance_id=r.instance_id)))
            settings.connection_file.chmod(0o600)
            configuration=tmp_path/'update.json';configuration.write_text(settings.model_dump_json());configuration.chmod(0o600)
            ident=str(uuid4());base=[str(python),'-I','-m','naiyue_test_agent.cli','cloud','release']
            launch=subprocess.run([*base,'update','r2','--command-id',ident,'--settings',str(configuration)],capture_output=True,text=True,timeout=10)
            assert launch.returncode==0,launch.stderr
            assert json.loads(launch.stdout)['command_id']==ident
            until(entered.exists)
            assert json.loads(entered.read_text())['sid']!=os.getsid(0)
            store=UpdateStore(settings)
            assert store.read(ident)['phase']=='running'
            gate.touch()
            failed=until(lambda:store.read(ident) if store.read(ident)['phase']=='failed' else None)
            if protocol==2:
                assert failed['step']=='building' and failed['error_code']=='template_preflight_failed'
                assert r.releases.active('python-tests')['revision']==0 and r.releases.repository.list()==[]
                return
            assert failed['step']=='activating' and r.releases.active('python-tests')['revision']==1
            resume=subprocess.run([*base,'resume',ident,'--settings',str(configuration)],capture_output=True,text=True,timeout=10)
            assert resume.returncode==0,resume.stderr
            final=until(lambda:store.read(ident) if store.read(ident)['phase']=='completed' else None)
            assert final['manifest_sha256'] and r.releases.active('python-tests')['revision']==1
            recorded=[json.loads(x) for x in calls.read_text().splitlines()]
            assert sum('build' in args for args in recorded)==1
            assert sum('run' in args for args in recorded)==2
