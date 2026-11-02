"""Read-only batched Git metadata using the existing bounded process runner."""
import os
from pathlib import Path
import shutil
import tempfile
import logging

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.tools.processes import HostProcessRunner,ProcessRequest,run_async_sync

logger=logging.getLogger(__name__)


class SourceGit:
    def __init__(self,root,limits):self.root,self.limits,self.commands=root,limits,0

    def run(self,args,*,data=None,optional=False):
        executable=shutil.which(self.limits.git_executable)
        if executable is None:raise CloudError('input_git_unavailable')
        env={k:v for k,v in os.environ.items() if k.upper() in ('PATH','HOME','USERPROFILE','SYSTEMROOT','TEMP','TMP','TMPDIR','XDG_CONFIG_HOME')}
        env.update(GIT_TERMINAL_PROMPT='0',GIT_OPTIONAL_LOCKS='0',GIT_ATTR_NOSYSTEM='1')
        output=bytearray();errors=0
        async def consume(kind,chunk):
            nonlocal errors
            if kind=='stderr':errors+=len(chunk)
            else:output.extend(chunk)
            if max(len(output),errors)>self.limits.max_metadata_bytes*4:raise CloudError('input_metadata_budget')
        with tempfile.TemporaryFile() as stream:
            if data is not None:stream.write(data);stream.seek(0)
            request=ProcessRequest([executable,'--no-optional-locks','-c','core.hooksPath='+os.devnull,
                '-c','core.fsmonitor=false','-c','protocol.allow=never',*args],Path(self.root),env,
                self.limits.git_timeout_seconds,stdin_fd=stream.fileno() if data is not None else None)
            self.commands+=1
            try:result=run_async_sync(lambda:HostProcessRunner().run(request,consume,None))
            except BaseException as error:
                logger.warning('cloud_source_git_failed error_type=%s',type(error).__name__);raise
        if result.termination!='exited' or result.returncode not in ((0,1,128) if optional else (0,)):
            logger.warning('cloud_source_git_rejected exit_code=%s',result.returncode)
            raise CloudError('input_git_failed')
        return bytes(output) if result.returncode==0 else b''
