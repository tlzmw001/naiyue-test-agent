"""Compare fixed local Git calls without changing the installed Agent or Docker."""
import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import inspect
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

GROUPS=['empty_loop_per_call','native_same_git_request','host_runner_per_call',
    'host_runner_shared_loop','git_repository_per_call','git_repository_shared_loop']


def native_call(request,expected):
    options=({'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW}
        if os.name=='nt' else {'start_new_session':True})
    result=subprocess.run(request.argv,cwd=request.cwd,env=request.env,
        timeout=request.timeout_seconds,stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,**options)
    if result.returncode!=0 or result.stdout!=expected or result.stderr:
        raise RuntimeError('Git output verification failed')


async def host_call(runner,request,expected):
    output=bytearray();errors=bytearray()
    async def consume(stream,chunk):
        (errors if stream=='stderr' else output).extend(chunk)
    result=await runner.run(request,consume)
    if (result.termination!='exited' or result.returncode!=0 or not result.output_complete
            or not result.process_reaped or not result.pipes_closed or not result.consumer_settled):
        raise RuntimeError('Git process settlement failed')
    if bytes(output)!=expected or errors:raise RuntimeError('Git output verification failed')


@contextmanager
def shared_git_loop():
    with asyncio.Runner() as runner:
        with patch('naiyue_test_agent.execution.git_repository.run_async_sync',lambda call:runner.run(call())):
            yield


def measure(name,call,runs,report):
    report['phase']=name;started=time.monotonic();samples=[]
    group=report['groups'][name]={'count':0,'samples_ms':samples}
    try:
        for _ in range(runs):
            before=time.monotonic();call()
            samples.append(round((time.monotonic()-before)*1000,3))
            group['count']+=1
    finally:
        group['group_seconds']=round(time.monotonic()-started,6)
        if samples:
            group.update(min_ms=min(samples),median_ms=statistics.median(samples),max_ms=max(samples))


def scenario(directory,executable,runs,timeout,report):
    from naiyue_test_agent import __version__
    from naiyue_test_agent.execution.git_repository import GitRepository
    from naiyue_test_agent.sandbox_settings import GitInputSettings
    from naiyue_test_agent.tools.processes import HostProcessRunner,run_async_sync
    report.update(controller_version=__version__,python_version=sys.version.split()[0],
        platform=sys.platform,provider_requests=0,groups={},source_sha256={})
    for name,target in [('git_repository',GitRepository),('host_processes',HostProcessRunner),
            ('sync_bridge',run_async_sync)]:
        report['source_sha256'][name]=hashlib.sha256(Path(inspect.getfile(target)).read_bytes()).hexdigest()
    class CaptureRunner(HostProcessRunner):
        async def run(self,request,consume,control=None):
            self.request=request
            return await super().run(request,consume,control)
    report['phase']='setup';started=time.monotonic();capture=CaptureRunner()
    settings=GitInputSettings(executable=executable,timeout_seconds=timeout)
    expected=GitRepository(directory,settings,runner=capture).run(['--version'])
    request=capture.request
    if not expected.startswith(b'git version ') or request.stdin_fd is not None:
        raise RuntimeError('Unexpected Git setup response')
    report.update(setup_seconds=round(time.monotonic()-started,6),
        output_sha256=hashlib.sha256(expected).hexdigest(),output_bytes=len(expected),runs=runs)
    async def noop():pass
    measure(GROUPS[0],lambda:asyncio.run(noop()),runs,report)
    measure(GROUPS[1],lambda:native_call(request,expected),runs,report)
    host=HostProcessRunner()
    measure(GROUPS[2],lambda:asyncio.run(host_call(host,request,expected)),runs,report)
    started=time.monotonic()
    with asyncio.Runner() as runner:
        measure(GROUPS[3],lambda:runner.run(host_call(host,request,expected)),runs,report)
    report['groups'][GROUPS[3]]['group_seconds']=round(time.monotonic()-started,6)
    repository=GitRepository(directory,settings)
    def git_call():
        if repository.run(['--version'])!=expected:raise RuntimeError('Git output verification failed')
    measure(GROUPS[4],git_call,runs,report)
    started=time.monotonic()
    with shared_git_loop():measure(GROUPS[5],git_call,runs,report)
    report['groups'][GROUPS[5]]['group_seconds']=round(time.monotonic()-started,6)
    report['phase']='completed'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--git-executable',default='git')
    parser.add_argument('--runs',type=int,default=5,choices=range(1,11))
    parser.add_argument('--timeout-seconds',type=int,default=10,choices=range(1,61))
    args=parser.parse_args(argv)
    if not __debug__:raise RuntimeError('Run without -O')
    if args.output.exists():raise FileExistsError('Choose a fresh report path')
    report=dict(schema_version=1,status='running',phase='setup',provider_requests=0);result=0
    try:
        with tempfile.TemporaryDirectory(prefix='nyt-git-cost-') as directory:
            scenario(Path(directory).resolve(),args.git_executable,args.runs,args.timeout_seconds,report)
        report['status']='measured'
    except (KeyboardInterrupt,asyncio.CancelledError) as error:
        result=130;report.update(status='cancelled',error_type=type(error).__name__)
    except Exception as error:
        result=1;report.update(status='failed',error_type=type(error).__name__)
    finally:
        with args.output.open('x',encoding='utf-8') as output:json.dump(report,output,indent=2)
        print(json.dumps(report,indent=2))
    return result


if __name__=='__main__':raise SystemExit(main())
