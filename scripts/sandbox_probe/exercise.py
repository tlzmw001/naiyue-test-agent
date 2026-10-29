"""Fixed, opt-in G1 checks for a disposable, labelled test sandbox only."""
import asyncio
import base64
import json
import time
from uuid import uuid4

from .transport import ProbeError


def output_bytes(value):
    data=base64.b64decode(value['bytes_base64'],validate=True)
    if len(data)>65536:raise ValueError('probe_output_limit')
    return data


def confirmed_stop(row):
    result=row.get('result') or {}
    return (row.get('state')=='stopped' and result.get('ok') is True
        and result.get('value',{}).get('process_reaped') is True)


def payload(code):
    return {'args':['-u','-c',code],'timeout':30,'settings':{'executable':'python','prefix_args':[]},
        'process_settings':None}


async def terminal(channel,ident,*,timeout=30):
    async with asyncio.timeout(timeout):
        while True:
            row=await channel.request('operation_status',{'operation_id':ident})
            if row['state'] not in ('starting','running'):return row
            await asyncio.sleep(.05)


async def read_output(channel,ident):
    return output_bytes(await channel.request('operation_output',{'operation_id':ident,'offset':0,'limit':65536}))


async def command(channel,code):
    ident=uuid4().hex
    await channel.request('operation_submit',{'operation_id':ident,'kind':'argv','payload':payload(code)})
    row=await terminal(channel,ident)
    if row['state']!='completed' or not row.get('result',{}).get('ok'):
        raise ProbeError('probe_operation_failed')
    if row['result']['value'].get('returncode')!=0:raise ProbeError('probe_command_failed')
    return await read_output(channel,ident)


async def capabilities(channel):
    """Use the real envd process path, with default shell settings."""
    result={}
    for name,kind,value in (
        ('bash','shell',{'name':'bash','command':'printf spec141','timeout':10,'settings':{},'process_settings':None}),
        ('aitest','argv',{'args':['--help'],'timeout':10,'settings':{'executable':'aitest'},'process_settings':None})):
        ident=uuid4().hex
        await channel.request('operation_submit',{'operation_id':ident,'kind':kind,'payload':value})
        row=await terminal(channel,ident)
        details=row.get('result') or {}
        result[name]={'ok':row['state']=='completed' and details.get('ok') is True and details.get('value',{}).get('returncode')==0}
    return result


async def output_stress(channel,retention_limit=None):
    from .measure import latency_samples,distribution
    ident=uuid4().hex;size=200*1024*1024
    value=payload("import sys;block=b'x'*65536\nfor _ in range(3200):sys.stdout.buffer.write(block)\nsys.stdout.buffer.flush()")
    value['timeout']=60
    started=time.perf_counter()
    await channel.request('operation_submit',{'operation_id':ident,'kind':'argv','payload':value})
    async with asyncio.timeout(15):
        while True:
            row=await channel.request('operation_status',{'operation_id':ident})
            if row['observed_bytes'] or row['state'] not in ('starting','running'):break
            await asyncio.sleep(.05)
    before=row
    reads=await latency_samples(channel,'resource_info',{'path':'/workspace'},samples=40,concurrency=8)
    after=await channel.request('operation_status',{'operation_id':ident})
    overlap=before['state']=='running' and after['observed_bytes']>before['observed_bytes']
    row=await terminal(channel,ident,timeout=65)
    observed=row['observed_bytes']
    if row['state']!='completed' or observed!=size or row['result']['value']['returncode']!=0:
        raise ProbeError('probe_output_stress_failed')
    output=await channel.request('operation_output',{'operation_id':ident,'offset':0,'limit':65536})
    retained=observed-output['first_offset']
    if retention_limit is not None and retained>retention_limit:raise ProbeError('probe_output_retention_failed')
    # No output is consumed until completion: envd must retain only its bounded tail.
    return {'observed_bytes':observed,'seconds':round(time.perf_counter()-started,4),
        'retained_tail_bytes':retained,'retention_limit_bytes':retention_limit,
        'retention_check':'passed' if retention_limit is not None else 'not_verified',
        'bounded_return_bytes':len(output_bytes(output)),
        'small_io_batch':distribution(reads),'output_overlap_observed':overlap,
        'transport_slow_reader':'not_verified'}


# Opens the journal without changing bytes. Only fixed booleans cross into the report.
SECURITY_CODE='''import json,os,socket
result={'uid':os.getuid(),'journal_readwrite':False,'socket_connect':False,'controller_environ_read':False}
try:
    with open('/state/journal.sqlite','r+b'):result['journal_readwrite']=True
except PermissionError:pass
try:
    with socket.socket(socket.AF_UNIX) as s:
        s.connect('/tmp/naiyue-envd.sock');result['socket_connect']=True
except PermissionError:pass
try:
    with open('/proc/1/environ','rb') as f:
        result['controller_environ_read']=b'NYT_ENVIRONMENT=' in f.read(65536)
except PermissionError:pass
print(json.dumps(result))
'''


async def exercise(connect,report):
    channel=await connect()
    results=report['exercise']={}
    try:
        if channel.ready.get('owned') is not True:raise ValueError('owned probe channel required')
        print('probe: dedicated sandbox file/command checks',flush=True)
        marker='/workspace/spec141-'+uuid4().hex+'.txt'
        started=time.perf_counter()
        result=await command(channel,"from pathlib import Path;Path("+repr(marker)+").write_text('once');print('once')")
        content=output_bytes(await channel.request('read_resource',{'path':marker,'max_bytes':100}))
        if result!=b'once\n' or content!=b'once':raise ProbeError('probe_file_mismatch')
        results['command_and_read']={'ok':True,'seconds':round(time.perf_counter()-started,4)}
        results['capabilities']=await capabilities(channel)
        print('probe: 200 MiB output and concurrent small IO',flush=True)
        results['output_stress']=await output_stress(channel,report.get('output_retention_limit_bytes'))
        print('probe: disconnect/reconnect and original operation query',flush=True)
        ident=uuid4().hex
        long_command="import time;print('started',flush=True);time.sleep(25)"
        await channel.request('operation_submit',{'operation_id':ident,'kind':'argv','payload':payload(long_command)})
        async with asyncio.timeout(10):
            while b'started' not in await read_output(channel,ident):await asyncio.sleep(.05)
        await channel.close()
        channel=await connect()
        row=await channel.request('operation_status',{'operation_id':ident})
        results['disconnect_custody']={'ok':row['state']=='running','submissions':1,
            'lost_ack_injected':False}
        if row['state']!='running':raise ProbeError('probe_custody_failed')
        started=time.perf_counter()
        row=await channel.request('operation_stop',{'operation_id':ident})
        results['stop']={'ok':confirmed_stop(row),'seconds':round(time.perf_counter()-started,4)}
        if not confirmed_stop(row):raise ProbeError('probe_stop_unconfirmed')
        print('probe: actual workload permission checks',flush=True)
        facts=json.loads(await command(channel,SECURITY_CODE))
        keys={'uid','journal_readwrite','socket_connect','controller_environ_read'}
        if set(facts)!=keys or type(facts['uid']) is not int or any(type(facts[k]) is not bool for k in keys-{'uid'}):
            raise ProbeError('probe_security_response')
        results['workload_isolation']={**facts,'passed':not any(facts[k] for k in keys-{'uid'})}
        report.setdefault('budget_screen',{})['security_isolation']='passed' if results['workload_isolation']['passed'] else 'failed'
        # The current image is expected to fail this gate. Measurement success is not migration approval.
        report['migration_gate']='blocked_security' if not results['workload_isolation']['passed'] else 'pending_other_gates'
    finally:await channel.close()
