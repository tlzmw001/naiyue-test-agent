"""Explicit sample counts; no synthetic percentiles for missing evidence."""
import math
import statistics
import asyncio
import base64
import hashlib
import os
import time
import re

from .transport import ProbeError


def safe_version(value):
    return value if isinstance(value,str) and re.fullmatch(r'[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}',value) else None


def distribution(seconds):
    if not seconds:return dict(count=0,p50_ms=None,p95_ms=None,max_ms=None)
    values=sorted(seconds)
    return dict(count=len(values),p50_ms=round(statistics.median(values)*1000,3),
                p95_ms=round(values[math.ceil(len(values)*.95)-1]*1000,3),max_ms=round(values[-1]*1000,3))


async def latency_samples(channel,operation,params,*,samples,concurrency=1):
    values=[]
    async def one():
        started=time.perf_counter()
        await channel.request(operation,params)
        values.append(time.perf_counter()-started)
    for begin in range(0,samples,concurrency):
        await asyncio.gather(*(one() for _ in range(min(concurrency,samples-begin))))
    return values


async def transfer(channel,total,*,upload):
    initial=channel.sent_bytes if upload else channel.received_bytes
    started=time.perf_counter();pending=[]
    async def one(size):
        if upload:
            data=os.urandom(size)
            value=await channel.request('probe_sink',{'data':base64.b64encode(data).decode()})
            if value!={'bytes':size,'sha256':hashlib.sha256(data).hexdigest()}:raise ProbeError('probe_transfer_mismatch')
        else:
            value=await channel.request('probe_source',{'size':size})
            data=base64.b64decode(value['data'],validate=True)
            if len(data)!=size or hashlib.sha256(data).hexdigest()!=value['sha256']:raise ProbeError('probe_transfer_mismatch')
    for offset in range(0,total,65536):
        pending.append(asyncio.create_task(one(min(65536,total-offset))))
        if len(pending)==8:
            await asyncio.gather(*pending);pending=[]
    if pending:await asyncio.gather(*pending)
    elapsed=time.perf_counter()-started
    wire=(channel.sent_bytes if upload else channel.received_bytes)-initial
    return {'payload_bytes':total,'wire_bytes':wire,'seconds':round(elapsed,4),
        'payload_mbit_per_second':round(total*8/elapsed/1e6,3),
        'wire_mbit_per_second':round(wire*8/elapsed/1e6,3),'concurrency':8}


async def measure(connect,report,*,samples=200,connections=20,transfer_bytes=8*1024*1024,read_path=None):
    stages=report['stages'];latencies=[]
    print('probe: connection samples',flush=True)
    for _ in range(connections):
        started=time.perf_counter()
        async with await connect() as channel:await channel.request('hello',{})
        latencies.append(time.perf_counter()-started)
    stages['connect_hello_close']=distribution(latencies)
    async with await connect() as channel:
        hello=await channel.request('hello',{})
        report['envd_version']=safe_version(hello.get('package_version'))
        for name,operation,params,concurrency in (
            ('pipe_rtt','probe_ping',{},1),('envd_rtt','hello',{},1),
            ('resource_info','resource_info',{'path':'/workspace'},1),
            ('resource_info_parallel_8','resource_info',{'path':'/workspace'},8)):
            print('probe: '+name,flush=True)
            stages[name]=distribution(await latency_samples(channel,operation,params,samples=samples,concurrency=concurrency))
        if read_path is not None:
            print('probe: sample file read',flush=True)
            stages['sample_read']=distribution(await latency_samples(channel,'read_resource',
                {'path':read_path,'max_bytes':65536},samples=min(samples,20)))
        print('probe: memory upload/download (no sandbox files written)',flush=True)
        stages['memory_upload']=await transfer(channel,transfer_bytes,upload=True)
        stages['memory_download']=await transfer(channel,transfer_bytes,upload=False)
        stages['channel_counts']={'requests':channel.sent_count,'responses':channel.received_count,
            'stderr_bytes':channel.stderr_bytes}
    report['budget_screen']={
        'envd_rtt_within_100ms':stages['envd_rtt']['p95_ms']<=100,
        'connect_hello_close_within_2s':stages['connect_hello_close']['p95_ms']<=2000,
        'wire_upload_at_least_20mbit':stages['memory_upload']['wire_mbit_per_second']>=20,
        'wire_download_at_least_20mbit':stages['memory_download']['wire_mbit_per_second']>=20,
        'snapshot':'single_sample' if 'snapshot' in report else 'not_measured',
        'docker_create':'single_sample' if 'docker_create_start_seconds' in report else 'not_measured',
        'git_install':'single_sample' if 'input_install' in report else 'not_measured',
        'security_isolation':'not_verified','company_acceptance':'not_verified',
    }
