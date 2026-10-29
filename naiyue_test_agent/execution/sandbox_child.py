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
    if os.name!='nt':
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:return
    else:
        # Same native tree termination as HostProcessRunner; never kill every ssh.exe.
        killer=await asyncio.create_subprocess_exec('taskkill','/PID',str(process.pid),'/T','/F',
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            async with asyncio.timeout(5):status=await killer.wait()
            if status!=0:raise SandboxError('env_connection_cleanup_unconfirmed')
        finally:
            if killer.returncode is None:killer.kill();await killer.wait()


async def close_child(process):
    """Readers remain active until pipes drain; cleanup uncertainty stays observable."""
    try:
        if process.stdin:process.stdin.close()
        try:
            async with asyncio.timeout(2):await process.wait()
        except TimeoutError:
            await terminate_tree(process)
            async with asyncio.timeout(5):await process.wait()
        finally:
            if os.name!='nt':await terminate_tree(process)
    except (OSError,TimeoutError,SandboxError) as error:
        logger.error('sandbox_connection_cleanup_unconfirmed error_type=%s',type(error).__name__)
        raise SandboxError('env_connection_cleanup_unconfirmed') from error


async def spawn_child(argv,*,env,cwd,limit):
    from naiyue_test_agent.agent_runtime.async_utils import await_settled
    spawn=asyncio.create_task(asyncio.create_subprocess_exec(*argv,stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,cwd=cwd,limit=limit,**process_options()))
    try:return await asyncio.shield(spawn)
    except asyncio.CancelledError:
        process=await await_settled(spawn)
        drains=[asyncio.create_task(drain(stream)) for stream in (process.stdout,process.stderr)]
        try:await await_settled(close_child(process))
        finally:
            for task in drains:task.cancel()
            await asyncio.gather(*drains,return_exceptions=True)
        raise


async def drain(stream):
    while await stream.read(8192):pass
