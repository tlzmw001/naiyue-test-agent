"""Failure and control paths must be observed without tight timing windows."""
import asyncio
import os
import signal
import sys
import pytest
from test_sandbox_probe_transport import IDENTITY,program


def test_control_request_completes_with_eight_io_requests_in_flight():
    from scripts.sandbox_probe.transport import ProbeChannel
    source=program("rows=[json.loads(sys.stdin.readline()) for _ in range(9)]\n"
        "for r in sorted(rows,key=lambda r:r['operation']!='operation_stop'):\n"
        " r.update(ok=True,result=r['operation']);print(json.dumps(r),flush=True)\n"
        "sys.stdin.read()\n")
    async def run():
        async with await ProbeChannel.open([sys.executable,'-u','-c',source],expected=IDENTITY,session_id='S',timeout=5) as c:
            result=await asyncio.gather(*(c.request('resource_info',{}) for _ in range(8)),c.request('operation_stop',{}))
            assert result==['resource_info']*8+['operation_stop']
    asyncio.run(run())


def test_worker_handler_failure_closes_idle_channel_promptly():
    from scripts.sandbox_probe.transport import ProbeChannel,ProbeError
    source="import scripts.sandbox_probe.worker as w\nasync def bad(*a,**k):return {'ok':True,'result':'x'*300000}\nw.dispatch=bad\nw.main()"
    async def run():
        import json
        env={**os.environ,'NYT_ENVIRONMENT':json.dumps(IDENTITY),'NYT_SESSION_ID':'S'}
        async with await ProbeChannel.open([sys.executable,'-u','-c',source],expected=IDENTITY,session_id='S',env=env,timeout=5) as c:
            with pytest.raises(ProbeError,match='probe_eof'):await c.request('hello',{})
    asyncio.run(run())


@pytest.mark.skipif(os.name=='nt',reason='POSIX process-group cleanup; Windows reports unconfirmed forced cleanup')
def test_inspection_cleans_descendant_even_when_launcher_has_exited(tmp_path):
    from scripts.sandbox_probe.transport import run_command,ProbeError
    pid_file=tmp_path/'pid'
    code="import subprocess,sys,os;open(sys.argv[1],'w').write(str(os.getpid()));subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])"
    async def run():
        group=None
        try:
            with pytest.raises(ProbeError,match='probe_timeout'):
                await run_command([sys.executable,'-c',code,str(pid_file)],timeout=1)
            group=int(pid_file.read_text())
            # Group disappearance is state-polled; no fixed short sleep assertion.
            async with asyncio.timeout(5):
                while True:
                    try:os.killpg(group,0)
                    except ProcessLookupError:break
                    await asyncio.sleep(.05)
        finally:
            if group is None and pid_file.exists():group=int(pid_file.read_text())
            if group is not None:
                try:os.killpg(group,signal.SIGKILL)
                except ProcessLookupError:pass
    asyncio.run(run())


def test_remote_diagnostic_values_cannot_be_arbitrary_text():
    from scripts.sandbox_probe.measure import safe_version
    from scripts.sandbox_probe.transport import remote_error_code
    assert safe_version('/private/project-path') is None
    assert safe_version('0.5.3')=='0.5.3'
    assert remote_error_code('private_customer_secret')=='probe_remote_error'
    assert remote_error_code('env_resource_unavailable')=='env_resource_unavailable'


@pytest.mark.skipif(os.name=='nt',reason='POSIX child-group pipe lifecycle')
def test_channel_close_observes_pipe_eof_after_launcher_exits():
    from scripts.sandbox_probe.transport import ProbeChannel
    source=program("import subprocess\nsubprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n")
    async def run():
        channel=await ProbeChannel.open([sys.executable,'-u','-c',source],expected=IDENTITY,session_id='S')
        async with asyncio.timeout(5):
            while channel.process.returncode is None:await asyncio.sleep(.05)
        await channel.close()
        assert channel.process.stdout.at_eof() and channel.process.stderr.at_eof()
    asyncio.run(run())


def test_worker_backpressure_fails_boundedly_and_reaps_requests():
    import json
    from scripts.sandbox_probe.transport import process_options
    source="import asyncio\nfrom scripts.sandbox_probe.worker import serve\nasyncio.run(serve("+repr(IDENTITY)+",'S','unused',timeout=.25))"
    async def run():
        process=await asyncio.create_subprocess_exec(sys.executable,'-u','-c',source,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,**process_options())
        try:
            ready=json.loads(await asyncio.wait_for(process.stdout.readline(),5))
            assert ready['probe']=='ready'
            for index in range(8):
                request=dict(protocol=2,request_id=str(index),environment=IDENTITY,session_id='S',operation='probe_source',params={'size':65536})
                process.stdin.write(json.dumps(request).encode()+b'\n')
            await process.stdin.drain()
            # Stop consuming stdout; stderr is the event indicating backpressure expiry.
            error=await asyncio.wait_for(process.stderr.readline(),5)
            assert error==b'probe_worker_request_failed\n'
            process.stdin.close()
            await asyncio.wait_for(asyncio.gather(process.stdout.read(),process.stderr.read(),process.wait()),5)
            assert process.returncode!=0
        finally:
            if process.returncode is None:process.kill();await process.wait()
    asyncio.run(run())
