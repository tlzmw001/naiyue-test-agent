"""Custody of a native SSH/docker child and its process tree, without a worker thread."""
import asyncio
import logging
import os
import signal
import subprocess

from naiyue_test_agent.contracts.sandbox import SandboxError

logger=logging.getLogger(__name__)


def process_options():
    if os.name=='nt':return dict(creationflags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW)
    return dict(start_new_session=True)


async def terminate_tree(process):
    await asyncio.sleep(0)
    if os.name!='nt':
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:return
    else:
        if process.returncode is not None:return
        # Retaining Popen's original handle pins this process ID until cleanup.
        if getattr(process,'_native_owner',None) is None:
            raise SandboxError('env_connection_cleanup_unconfirmed')
        # Same native tree termination as HostProcessRunner; never kill every ssh.exe.
        killer=await asyncio.create_subprocess_exec('taskkill','/PID',str(process.pid),'/T','/F',
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        process._cleanup_helpers=[helper for helper in getattr(process,'_cleanup_helpers',[]) if helper.returncode is None]+[killer]
        try:
            status=await killer.wait()
            await asyncio.sleep(0)
            if status!=0 and process.returncode is None:raise SandboxError('env_connection_cleanup_unconfirmed')
        finally:
            if killer.returncode is None:killer.kill()


async def close_child(process,*,timeout=10,attempts=2,readers=()):
    """Readers remain active until pipes drain; cleanup uncertainty stays observable."""
    try:
        async def settled():
            await process.wait()
            for helper in getattr(process,'_cleanup_helpers',[]):await helper.wait()
            if readers:await asyncio.gather(*(asyncio.shield(task) for task in readers))
        deadline=asyncio.get_running_loop().time()+timeout
        async with asyncio.timeout_at(deadline):
            if process.stdin:process.stdin.close()
            try:
                async with asyncio.timeout(min(2,timeout/3)):await settled()
            except TimeoutError:pass
            else:
                process._native_owner=None;return
            for attempt in range(attempts):
                await asyncio.sleep(0)
                if os.name!='nt' or process.returncode is None:
                    try:await terminate_tree(process)
                    except (OSError,SandboxError) as error:
                        logger.warning('sandbox_termination_attempt_failed attempt=%s error_type=%s',attempt+1,type(error).__name__)
                        continue
                remaining=deadline-asyncio.get_running_loop().time()
                try:
                    async with asyncio.timeout(remaining/(attempts-attempt)):await settled()
                except TimeoutError:continue
                process._native_owner=None;return
            raise SandboxError('env_connection_cleanup_unconfirmed')
    except (OSError,TimeoutError,SandboxError) as error:
        logger.error('sandbox_connection_cleanup_unconfirmed error_type=%s',type(error).__name__)
        raise SandboxError('env_connection_cleanup_unconfirmed') from error


async def spawn_child(argv,*,env,cwd,limit):
    from naiyue_test_agent.agent_runtime.async_utils import await_settled
    spawn=asyncio.create_task(asyncio.create_subprocess_exec(*argv,stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,cwd=cwd,limit=limit,**process_options()))
    try:
        process=await asyncio.shield(spawn)
        # Keep the original native process object, never reopen it by PID.
        process._native_owner=process._transport.get_extra_info('subprocess')
        return process
    except asyncio.CancelledError:
        process=await await_settled(spawn)
        process._native_owner=process._transport.get_extra_info('subprocess')
        drains=[asyncio.create_task(drain(stream)) for stream in (process.stdout,process.stderr)]
        try:await await_settled(close_child(process,readers=drains))
        finally:
            for task in drains:task.cancel()
            await asyncio.gather(*drains,return_exceptions=True)
        raise


async def drain(stream):
    while await stream.read(8192):pass
