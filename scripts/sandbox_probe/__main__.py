"""Read-only performance probe for an explicitly selected existing sandbox."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

from .target import Target,inspect_target
from .transport import ProbeChannel,ProbeError
from .measure import measure


def save_report(path,value):
    # Exclusive create: reports cannot overwrite configuration or prior evidence.
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2,ensure_ascii=True);stream.write('\n')


async def run(args,report):
    target=Target(args.container,ssh_target=args.ssh_target,context=args.context,
        ssh_executable=args.ssh_exe,docker_executable=args.docker_exe,socket_path=args.docker_socket)
    target,expected,session_id=await inspect_target(target,owned=args.exercise_owned)
    async def connect():
        return await ProbeChannel.open(target.worker_command(owned=args.exercise_owned),expected=expected,session_id=session_id,
            timeout=args.timeout,limit=2097152 if args.exercise_owned else 262144)
    report['transport']='ssh' if target.ssh_target else 'local_docker'
    report['scope']='existing_container_readonly'
    await measure(connect,report,samples=args.samples,connections=args.connections,
        transfer_bytes=args.transfer_mib*1024*1024,read_path=args.read_path)
    if args.exercise_owned:
        from .exercise import exercise
        report['scope']='dedicated_sandbox_mutating_probe'
        await exercise(connect,report)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container',required=True)
    parser.add_argument('--ssh-target');parser.add_argument('--ssh-exe',default='ssh')
    parser.add_argument('--context',default='default');parser.add_argument('--docker-exe',default='docker')
    parser.add_argument('--docker-socket',default='/var/run/docker.sock')
    parser.add_argument('--samples',type=int,default=200);parser.add_argument('--connections',type=int,default=20)
    parser.add_argument('--transfer-mib',type=int,default=8);parser.add_argument('--timeout',type=float,default=15)
    parser.add_argument('--read-path');parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--exercise-owned',action='store_true',help='Fixed mutation tests; dedicated naiyue.probe=spec141 sandbox ONLY')
    args=parser.parse_args(argv)
    if not (1<=args.connections<=50 and 20<=args.samples<=2000 and 1<=args.transfer_mib<=32 and 1<=args.timeout<=60):
        parser.error('probe budgets are out of range')
    if args.output.exists():parser.error('output already exists; choose a new report path')
    if not args.output.parent.is_dir():parser.error('output parent does not exist')
    report={'schema_version':1,'status':'running','stages':{},'provider_requests':0,
        'worker_sha256':hashlib.sha256(Path(__file__).with_name('worker.py').read_bytes()).hexdigest()}
    started=time.perf_counter();exit_code=0
    try:
        asyncio.run(run(args,report));report['status']='measured'
    except (Exception,KeyboardInterrupt) as error:
        report['status']='failed';exit_code=2
        report['error_code']=str(error) if isinstance(error,ProbeError) else 'probe_'+type(error).__name__.lower()
        report['error_errno']=getattr(error,'safe_errno',None) if isinstance(error,ProbeError) else None
    finally:
        report['total_seconds']=round(time.perf_counter()-started,3)
        save_report(args.output,report)
    print('probe: '+report['status']+'; sanitized JSON report saved',flush=True)
    if report['status']=='failed':print('error_code: '+report['error_code'],flush=True)
    return exit_code


if __name__=='__main__':raise SystemExit(main())
