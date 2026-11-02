"""Small S00 SDK helpers; deliberately independent of A/envd transport."""
import asyncio
import json
import logging
from pathlib import Path
import shlex

from scripts.cloud_probe.checks import require


def command_text(payload):
    # execd v1.1.0 accepts command text, but not the SDK's newer argv wire field.
    source=Path(__file__).with_name('task.py').read_text(encoding='utf-8')
    return shlex.join(['python','-I','-c',source,json.dumps(payload)])


async def command(sandbox,payload,*,timeout=20):
    from datetime import timedelta
    from opensandbox.models.execd import RunCommandOpts
    async with asyncio.timeout(timeout+5):
        result=await sandbox.commands.run(command_text(payload),
            opts=RunCommandOpts(timeout=timedelta(seconds=timeout)))
    require(result.error is None and result.exit_code==0,'task_command_failed')
    text=''.join(line.text for line in result.logs.stdout)
    require(len(text)<=65536,'task_output_limit')
    return json.loads(text)


async def close_owned(sandbox,run_id):
    try:
        info=await sandbox.get_info()
        require((info.metadata or {}).get('nyt-s00')==run_id,'cleanup_ownership_mismatch')
    except BaseException:
        try:await sandbox.close()
        except Exception as error:
            logging.getLogger(__name__).error('s00_local_close_failed error_type=%s',type(error).__name__)
        raise
    await sandbox.destroy()
