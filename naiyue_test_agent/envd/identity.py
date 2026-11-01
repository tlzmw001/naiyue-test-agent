"""Trusted container identity policy; task requests cannot select credentials."""
from dataclasses import replace
import logging
import os
from pathlib import Path
import stat
import sys
import shutil

from naiyue_test_agent.contracts.sandbox import (
    SandboxError, SANDBOX_ROOT, STATE_ROOT, WORK_VOLUME_ROOT, TASK_UID, TASK_GID,
)
from naiyue_test_agent.tools.processes import HostProcessRunner
from naiyue_test_agent.diagnostics import safe_log

logger = logging.getLogger(__name__)


def _capability_status():
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    return {key: int(fields[key], 10 if key == 'NoNewPrivs' else 16)
        for key in ('CapEff', 'CapPrm', 'CapBnd', 'CapInh', 'CapAmb', 'NoNewPrivs')}


def _clear_inheritable():
    import ctypes
    class Header(ctypes.Structure):
        _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]
    library = ctypes.CDLL(None, use_errno=True)
    header, data = Header(0x20080522, 0), (Data * 2)()
    for name in ('capget', 'capset'):
        function = getattr(library, name)
        function.argtypes = [ctypes.POINTER(Header), ctypes.POINTER(Data)]
        function.restype = ctypes.c_int
    if library.capget(ctypes.byref(header), data) != 0:
        raise OSError(ctypes.get_errno(), 'capget_failed')
    for word in data: word.inheritable = 0
    if library.capset(ctypes.byref(header), data) != 0:
        raise OSError(ctypes.get_errno(), 'capset_failed')


def initialize_capabilities():
    expected = sum(1 << bit for bit in (0, 1, 3, 5, 6, 7))
    phase, status = 'initial', {}
    try:
        status = _capability_status()
        if (any(status[key] != expected for key in ('CapEff', 'CapPrm', 'CapBnd'))
                or status['CapInh'] & ~expected or status['CapAmb'] or status['NoNewPrivs'] != 1):
            raise SandboxError('env_policy_mismatch')
        if status['CapInh']:
            _clear_inheritable()
        phase, status = 'normalized', _capability_status()
        if (any(status[key] != expected for key in ('CapEff', 'CapPrm', 'CapBnd'))
                or status['CapInh'] or status['CapAmb'] or status['NoNewPrivs'] != 1):
            raise SandboxError('env_policy_mismatch')
    except (OSError, ValueError, KeyError, AttributeError, SandboxError) as error:
        safe_log(logger.error, 'envd_identity_rejected phase=%s capabilities=%s errno=%s error_type=%s',
            phase, status, getattr(error, 'errno', None), type(error).__name__)
        if isinstance(error, SandboxError): raise
        raise SandboxError('env_policy_mismatch') from error


class TaskProcessRunner(HostProcessRunner):
    async def run(self, request, consume, control=None):
        # serve() requires root and the exact capability set. Non-root execution is
        # used only by native unit fixtures, never as a production fallback.
        identity = (TASK_UID,TASK_GID) if os.geteuid() == 0 else None
        environment = {key:value for key,value in request.env.items() if not key.startswith('NYT_')}
        environment.update(HOME=SANDBOX_ROOT if identity else str(request.cwd))
        return await super().run(replace(request,identity=identity,env=environment),consume,control)


def trusted_git_executable(value):
    executable=shutil.which(value)
    if executable is None:raise SandboxError('env_policy_mismatch')
    path=Path(executable).resolve(strict=True)
    if any(path.is_relative_to(root) for root in (WORK_VOLUME_ROOT,STATE_ROOT,'/tmp','/proc','/dev','/sys')):
        raise SandboxError('env_policy_mismatch')
    if not stat.S_ISREG(path.stat().st_mode):raise SandboxError('env_policy_mismatch')
    for part in (path,*path.parents):
        info=part.stat()
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode)&0o022:
            raise SandboxError('env_policy_mismatch')
    return str(path)


def check_private_directory(path):
    info = Path(path).lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise SandboxError('env_policy_mismatch')


def initialize_identity(environment):
    if sys.platform != 'linux' or os.geteuid() != 0 or environment.root != SANDBOX_ROOT:
        raise SandboxError('env_policy_mismatch')
    initialize_capabilities()
    volume = Path(WORK_VOLUME_ROOT)
    info = volume.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
        raise SandboxError('env_policy_mismatch')
    workspace = volume/'workspace'
    if not workspace.exists():
        workspace.mkdir(mode=0o755)
        os.chown(workspace,TASK_UID,TASK_GID)
    info = workspace.lstat()
    if (not stat.S_ISDIR(info.st_mode) or (info.st_uid,info.st_gid) != (TASK_UID,TASK_GID)
            or Path(SANDBOX_ROOT).resolve(strict=True) != workspace):
        raise SandboxError('env_policy_mismatch')
    stage = volume/'.envd-stage'
    stage.mkdir(mode=0o700,exist_ok=True)
    check_private_directory(stage)
    check_private_directory(STATE_ROOT)
    os.umask(0o077)
