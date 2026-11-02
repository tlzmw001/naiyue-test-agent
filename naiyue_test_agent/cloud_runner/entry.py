"""Wheel-owned foreground launcher, copied into the trusted template root."""
import asyncio
import logging
from pathlib import Path
import signal
import time

from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.cloud_runner import RunnerBootstrap
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.cloud_runner.state import read_json,write_json
from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.cloud_runner.transport import RunnerClient

logger=logging.getLogger(__name__)


async def serve(*,catalog=None):
    path=Path('/run/nyt/bootstrap.json');state=Path('/state');b=None
    try:
        b=RunnerBootstrap.model_validate(await run_blocking(read_json,path))
        deadline=time.monotonic()+b.input_wait_seconds
        while True:
            try:receipt=await run_blocking(read_json,state/'input-install.json')
            except FileNotFoundError:receipt=None
            if receipt and receipt.get('status')=='installed' and receipt.get('input_id')==b.input_id:break
            if time.monotonic()>=deadline:raise CloudError('runner_input_not_ready')
            await asyncio.sleep(b.retry_seconds)
        stop=asyncio.Event();loop=asyncio.get_running_loop()
        for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,stop.set)
        try:
            async with open_runner(b,Path('/workspace/project'),state,catalog=catalog) as kernel:
                await RunnerClient(kernel,bootstrap_path=path).run(stop)
            await run_blocking(write_json,state/'runner-exit.json',dict(task_id=b.task_id,generation=b.generation,
                boot_id=b.boot_id,code='runner_stopped',exit_code=0))
        finally:
            for sig in (signal.SIGINT,signal.SIGTERM):loop.remove_signal_handler(sig)
    except BaseException as error:
        code=error.code if isinstance(error,CloudError) else 'runner_exit_unconfirmed'
        logger.error('runner_exited code=%s error_type=%s',code,type(error).__name__)
        if b is not None:
            await run_blocking(write_json,state/'runner-exit.json',dict(task_id=b.task_id,generation=b.generation,
                boot_id=b.boot_id,code=code,exit_code=1))
        raise


def main(*,catalog=None):
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(name)s %(message)s')
    # Imported HTTP/WS libraries must not log URLs, headers, prompts or bodies.
    for name in ('httpx','httpcore','httpx2','httpcore2','websockets','openai','anthropic','ollama'):
        logging.getLogger(name).setLevel(logging.ERROR)
    try:asyncio.run(serve(catalog=catalog))
    except BaseException:return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
