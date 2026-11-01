"""Native controller-only checks; no Docker, SSH server, credentials or providers."""
import asyncio
import logging
import os
from pathlib import Path
import sys

if __package__:
    from .release_mcp_smoke import process_alive
else:
    from release_mcp_smoke import process_alive


WORKER = '''import asyncio,json,sys
sys.path.insert(0,sys.argv[3])
from naiyue_test_agent.envd.framing import FrameStream
class Reader:
 async def readline(self):return await asyncio.to_thread(sys.stdin.buffer.readline)
class Writer:
 def write(self,value):sys.stdout.buffer.write(value)
 async def drain(self):sys.stdout.buffer.flush()
async def main():
 environment=json.loads(sys.argv[1]);session=sys.argv[2];writer=Writer()
 writer.write(json.dumps(dict(attach=1,protocol=2,generation='native',environment=environment,session_id=session)).encode()+b'\\n')
 await writer.drain()
 frames=FrameStream(Reader(),writer,generation='native',frame_bytes=65536,message_bytes=1048576,max_pending=16)
 held=[]
 async def reply(ident,value):
  value.update(ok=True,result=value['params']);await frames.send(ident,value)
 try:
  while item:=await frames.receive():
   ident,value=item
   if value['operation']=='hold':held.append(item);continue
   if value['operation']=='hello':value['params']={'held':len(held)}
   if value['operation']=='operation_stop':
    value['params']={'released':len(held)}
    for saved in held:await reply(*saved)
    held.clear()
   await reply(ident,value)
 finally:await frames.close()
asyncio.run(main())
'''


def verify_sandbox_native_report(report):
    fields=('two_channels','late_reply','child_tree_cleanup','readonly_retirement')
    if (not isinstance(report,dict) or any(report.get(key)!='passed' for key in fields)
            or report.get('late_reply_proof')!={'held':1,'retired':1,'released':1,'remaining':0}):
        raise ValueError('Native sandbox verification incomplete')


async def sandbox_native_scenario(root):
    import json
    import naiyue_test_agent
    from naiyue_test_agent.execution.sandbox_channel import SandboxChannel
    from naiyue_test_agent.execution.sandbox_child import spawn_child,close_child,drain
    from naiyue_test_agent.application.environments.inputs import InputService
    root=Path(root).resolve();channels=[];report={};held=None
    try:
        for ident in ('native-a','native-b'):
            environment=dict(binding_id=ident,kind='sandbox',project_id='fixture',root='/workspace',
                platform='linux',project_revision=None)
            channels.append(await SandboxChannel.open([sys.executable,'-I','-u','-c',WORKER,
                json.dumps(environment),ident,str(Path(naiyue_test_agent.__file__).resolve().parent.parent)],
                environment=environment,session_id=ident,timeout=10,
                io_slots=1,message_bytes=1048576))
        first,second=channels
        value={'binary_text':'中文\0'+'x'*16000}
        assert await asyncio.gather(first.request('read',value),second.request('read',value))==[value,value]
        report['two_channels']='passed'
        held=asyncio.create_task(first.request('hold',{}))
        async with asyncio.timeout(10):
            while (await first.request('hello',{})).get('held')!=1:await asyncio.sleep(.01)
        held.cancel()
        try:await held
        except asyncio.CancelledError:pass
        else:raise AssertionError('Held waiter did not cancel')
        assert len(first.retired)==1
        released=await first.request('operation_stop',{})
        assert released=={'released':1}
        assert await first.request('read',value)==value and first.failed is None and not first.retired
        report['late_reply']='passed'
        report['late_reply_proof']={'held':1,'retired':1,'released':released['released'],'remaining':len(first.retired)}
        await first.close()
        assert first.process.returncode==0 and await second.request('read',{})=={}
    finally:
        primary=sys.exc_info()[1]
        if held is not None:
            held.cancel();await asyncio.gather(held,return_exceptions=True)
        results=await asyncio.gather(*(channel.close() for channel in channels),return_exceptions=True)
        failures=[]
        for result in results:
            if isinstance(result,BaseException):
                failures.append(result)
                logging.getLogger(__name__).error('native_channel_cleanup_failed error_type=%s',type(result).__name__)
        if failures and primary is None:raise failures[0]
    child=await spawn_child([sys.executable,'-I','-u','-c',
        'import subprocess,sys,time;p=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(600)"]);print(p.pid,flush=True);time.sleep(600)'],
        env=dict(os.environ),cwd=root,limit=65536)
    readers=[]
    try:
        async with asyncio.timeout(10):descendant=int(await child.stdout.readline())
    finally:
        primary=sys.exc_info()[1]
        readers=[asyncio.create_task(drain(stream)) for stream in (child.stdout,child.stderr)]
        try:await close_child(child,timeout=10,readers=readers)
        except Exception as error:
            logging.getLogger(__name__).error('native_tree_cleanup_failed error_type=%s',type(error).__name__)
            if primary is None:raise
    async with asyncio.timeout(10):
        while process_alive(descendant):await asyncio.sleep(.01)
    assert child.returncode is not None and all(task.done() for task in readers)
    report['child_tree_cleanup']='passed'
    directory=root/'native-private-input';directory.mkdir()
    (directory/'object').write_bytes(b'git-object');(directory/'object').chmod(0o444)
    service=object.__new__(InputService);service.directory=lambda *_:directory
    service._remove_directory('fixture','a'*32)
    assert not directory.exists();report['readonly_retirement']='passed'
    verify_sandbox_native_report(report)
    return report
