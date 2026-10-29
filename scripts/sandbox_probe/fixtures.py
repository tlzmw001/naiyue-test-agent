"""Generated local F1 fixture; requires project dependencies, never a real project."""
import base64
import os
from pathlib import Path
import time
from uuid import uuid4

from .exercise import terminal
from .transport import ProbeError


def populate(root):
    root=Path(root);root.mkdir()
    for index in range(651):
        (root/f'fixture-{index:04d}.txt').write_bytes(base64.b64encode(os.urandom(7680)))
    for group in ('.agents','.claude'):
        for index in range(10):
            skill=root/group/'skills'/f'skill-{index:02d}'
            skill.mkdir(parents=True)
            (skill/'SKILL.md').write_text(f'---\nname: skill-{index:02d}\ndescription: Generated probe fixture\n---\n'+('bounded instructions\n'*140))


def snapshot_fixture(directory,report):
    from naiyue_test_agent.contracts.projects import LocalSource
    from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder
    from naiyue_test_agent.execution.git_repository import GitRepository
    from naiyue_test_agent.sandbox_settings import GitInputSettings
    root=Path(directory).resolve();source=root/'source';populate(source)
    repository=GitRepository(source,GitInputSettings())
    for args in (['init','--quiet'],['add','.'],['-c','user.name=Spec141','-c','user.email=probe@example.invalid',
        '-c','commit.gpgsign=false','commit','--quiet','-m','generated fixture']):
        repository.run(args)
    started=time.perf_counter()
    snapshot=SnapshotBuilder(root/'snapshot',GitInputSettings()).prepare(LocalSource(source),uuid4().hex)
    report['snapshot']={'seconds':round(time.perf_counter()-started,4),'files':len(snapshot.manifest),
        'payload_bytes':snapshot.total_bytes,'bundle_bytes':snapshot.bundle_size,'samples':1}
    return snapshot,root/'snapshot'/'input.bundle'


async def install_fixture(channel,snapshot,bundle,report):
    if channel.ready.get('owned') is not True:raise ValueError('owned channel required')
    facts={key:getattr(snapshot,key) for key in ('input_id','original_head','input_snapshot')}
    transfer=uuid4().hex;started=time.perf_counter()
    value=await channel.request('input_begin',dict(transfer_id=transfer,**facts,size=snapshot.bundle_size,
        sha256=snapshot.bundle_sha256,base=None))
    if value!={'transfer_id':transfer,'offset':0}:raise ProbeError('probe_transfer_mismatch')
    offset=0;chunks=0
    with bundle.open('rb') as stream:
        while data:=stream.read(1048576):
            result=await channel.request('input_chunk',dict(transfer_id=transfer,offset=offset,data=base64.b64encode(data).decode()))
            offset+=len(data);chunks+=1
            if result!={'transfer_id':transfer,'offset':offset}:raise ProbeError('probe_transfer_mismatch')
    upload=time.perf_counter()-started;started=time.perf_counter()
    if await channel.request('input_cache',{'transfer_id':transfer})!=facts:raise ProbeError('probe_transfer_mismatch')
    cache=time.perf_counter()-started;started=time.perf_counter();ident=uuid4().hex
    await channel.request('operation_submit',{'operation_id':ident,'kind':'git_input','payload':facts})
    result=await terminal(channel,ident,timeout=60)
    if result['state']!='completed' or result.get('result')!={'ok':True,'value':facts}:raise ProbeError('probe_install_failed')
    report['input_install']={'upload_with_fsync_seconds':round(upload,4),'cache_validate_seconds':round(cache,4),
        'git_install_seconds':round(time.perf_counter()-started,4),'chunk_bytes':1048576,'chunks':chunks,'ok':True,'samples':1}
