"""Fixed-content local capture timing; not a target-machine acceptance test."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from naiyue_test_agent.cloud_files.capture import capture
from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.contracts.cloud_results import ResultBoundary,ResultLimits,ResultPlan


def run(count,rounds):
    with tempfile.TemporaryDirectory(prefix='nyt-result-profile-') as temporary:
        base=Path(temporary).resolve();source=base/'source';source.mkdir()
        paths=[]
        for index in range(count):
            name=f'f{index:05d}.txt';paths.append(name)
            (source/name).write_bytes(hashlib.sha256(str(index).encode()).hexdigest().encode()*128)
        limits=InputLimits();inputs=base/'input'
        captured=capture(source,{'paths':paths},inputs,limits)
        workspace=base/'workspace';shutil.copytree(source,workspace)
        (workspace/paths[0]).write_bytes(b'changed!'+(source/paths[0]).read_bytes()[8:])
        boundary=ResultBoundary(kind='run',epoch=str(uuid4()),seq=1,outcome='completed')
        task_id=uuid4().hex;project_id=str(uuid4());samples=[]
        for number in range(rounds+1):
            plan=ResultPlan(result_id=str(uuid4()),source_command_id=str(uuid4()),source_run_id=uuid4().hex,
                task_id=task_id,project_id=project_id,input_id=captured.input_id,source_generation=1,output_directory='.nyt-output')
            start=time.perf_counter()
            freeze_result(workspace,inputs,base/'spools'/str(number),plan,boundary,1,ResultLimits(),limits)
            samples.append(time.perf_counter()-start)
        warm=samples[1:]
        return {'files':count,'bytes_per_file':8192,'managed_bytes':count*8192,'changed_files':1,
            'changed_bytes':8192,'input_id':captured.input_id,'cold_seconds':samples[0],
            'warm_seconds':warm,'warm_p50_seconds':statistics.median(warm),
            'warm_p95_seconds':sorted(warm)[math.ceil(.95*len(warm))-1]}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--label',required=True);parser.add_argument('--rounds',type=int,default=20)
    args=parser.parse_args()
    if args.rounds<1:parser.error('--rounds must be positive')
    result={'label':args.label,'platform':platform.platform(),'machine':platform.machine(),'python':platform.python_version(),
        'git':subprocess.check_output(['git','--version'],text=True).strip(),'clock':'perf_counter',
        'method':f'Fresh task per size; first capture cold, then {args.rounds} distinct result IDs sharing I; all files read; 1 changed file; nearest-rank p95.',
        'target_2cpu_acceptance':'not_run','cases':[]}
    for count in (600,4000):
        case=run(count,args.rounds);result['cases'].append(case)
        print(json.dumps({k:v for k,v in case.items() if k!='warm_seconds'}),flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
