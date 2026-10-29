"""Bounded native Git commands; no hooks, filters, prompts or source credentials exported."""
from contextlib import ExitStack
import hashlib
import logging
import os
from pathlib import Path
import shutil
import tempfile
from time import monotonic
from naiyue_test_agent.diagnostics import safe_log, diagnostic_text, stderr_facts, command_kind

from naiyue_test_agent.contracts.git_inputs import GitInputError, git_path, object_id
from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessRequest, run_async_sync
from naiyue_test_agent.storage.files import open_store_readonly

logger = logging.getLogger(__name__)


class GitRepository:
    def __init__(self, path, settings, *, runner=None):
        self.path, self.settings = Path(path), settings
        self.runner = runner

    def run(self, args, *, cwd=None, data=None, input_stream=None, extra_env=None, credentials=False,
            source_config=False, check=True, control=None, output_limit=None, output_stream=None):
        if data is not None and input_stream is not None:
            raise ValueError('Git command accepts one input source')
        executable = shutil.which(self.settings.executable)
        if executable is None:
            raise GitInputError('git_input_git_unavailable')
        env = {key: value for key, value in os.environ.items()
               if key in ('PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'TMPDIR', 'TEMP', 'TMP', 'SSH_AUTH_SOCK')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT='0', GIT_PAGER='cat', GIT_OPTIONAL_LOCKS='0',
                   GIT_ATTR_NOSYSTEM='1', GIT_NO_REPLACE_OBJECTS='1')
        if credentials or source_config:
            env.pop('GIT_CONFIG_GLOBAL')
            env.pop('GIT_CONFIG_NOSYSTEM')
        env['GIT_ALLOW_PROTOCOL']='https:ssh:file'
        if extra_env:
            env.update(extra_env)
        prefix = ['-c', 'core.hooksPath='+os.devnull, '-c', 'core.fsmonitor=false',
                  '-c', 'core.attributesFile='+os.devnull,
                  '-c', 'core.autocrlf=false', '-c', 'core.safecrlf=false', '-c', 'core.protectNTFS=true',
                  '-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always',
                  '-c', 'protocol.https.allow=always', '-c', 'protocol.ssh.allow=always',
                  '-c', 'fetch.recurseSubmodules=false', '-c', 'submodule.recurse=false',
                  '-c', 'init.templateDir=', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
                  '-c', 'fetch.unpackLimit=0', '-c', 'transfer.unpackLimit=0',
                  '-c', 'fetch.fsckObjects=true', '-c', 'transfer.fsckObjects=true']
        if not source_config:
            prefix.extend(['-c','core.excludesFile='+os.devnull])
        output, errors = bytearray(), bytearray()
        error_size = output_size = 0

        async def consume(stream, chunk):
            nonlocal error_size, output_size
            if stream == 'stderr':
                error_size += len(chunk)
            else:
                output_size += len(chunk)
            target = errors if stream == 'stderr' else output
            limit = self.settings.max_command_bytes if stream == 'stderr' or output_limit is None else output_limit
            if (error_size if stream == 'stderr' else output_size) > limit:
                raise GitInputError('git_input_budget')
            if stream != 'stderr' and output_stream is not None:
                output_stream.write(chunk)
            else:
                target.extend(chunk)

        with ExitStack() as stack:
            source = input_stream
            if data is not None:
                source = stack.enter_context(tempfile.TemporaryFile())
                source.write(data); source.seek(0)
            elif source is not None:
                source.seek(0)
            request = ProcessRequest([executable, *prefix, *args], Path(cwd or self.path), env,
                self.settings.timeout_seconds, stdin_fd=source.fileno() if source else None)
            started = monotonic()
            facts = dict(command=command_kind(args, 'git'), timeout_seconds=request.timeout_seconds)
            try:
                result = run_async_sync(lambda: (self.runner or HostProcessRunner()).run(request, consume, control))
            except Exception as error:
                safe_log(logger.warning, 'git_input_command_failed error_type=%s diagnostic=%s', type(error).__name__,
                    diagnostic_text(error=error, phase='execute', duration_ms=int((monotonic()-started)*1000),
                        **facts, **stderr_facts(errors, error_size)))
                raise
        if result.termination != 'exited':
            safe_log(logger.warning, 'git_input_command_unfinished diagnostic=%s', diagnostic_text(
                termination=result.termination, exit_code=result.returncode, duration_ms=int((monotonic()-started)*1000),
                **facts, **stderr_facts(errors, error_size)))
            raise GitInputError('git_input_timeout')
        if result.returncode and check:
            safe_log(logger.warning, 'git_input_command_rejected exit_code=%s diagnostic=%s', result.returncode,
                diagnostic_text(phase='execute', duration_ms=int((monotonic()-started)*1000), **facts, **stderr_facts(errors, error_size)))
            raise GitInputError('git_input_git_failed')
        return bytes(output) if check else (result.returncode, bytes(output))

    def oid(self, ref):
        return object_id(self.run(['rev-parse', '--verify', ref]).decode().strip())

    def oid_from_index(self, env):
        return object_id(self.run(['write-tree'],extra_env=env).decode().strip())

    def tree(self, ref):
        result = []
        for entry in self.run(['ls-tree', '-rz', '--full-tree', ref]).split(b'\0'):
            if not entry:
                continue
            metadata, raw_path = entry.split(b'\t', 1)
            mode, kind, oid = metadata.decode('ascii').split(' ')
            path = git_path(raw_path)
            if kind != 'blob' or mode not in ('100644', '100755', '120000'):
                raise GitInputError('git_input_unsupported', (path,))
            result.append((path, mode, object_id(oid)))
        if len(result) > self.settings.max_files:
            raise GitInputError('git_input_budget')
        return result

    def has(self, oid):
        object_id(oid)
        return self.run(['cat-file', '-e', oid+'^{commit}'], check=False)[0] == 0


def bundle_digest(path, limit):
    size, digest = 0, hashlib.sha256()
    with os.fdopen(open_store_readonly(path), 'rb') as stream:
        while chunk := stream.read(1024*1024):
            size += len(chunk)
            if size > limit:
                raise GitInputError('git_input_budget')
            digest.update(chunk)
    return size, digest.hexdigest()


def check_cache_budget(directory, limit):
    if limit < 0:
        raise GitInputError('git_input_budget')
    total = 0
    for root, directories, files in os.walk(directory, followlinks=False):
        for name in [*directories, *files]:
            path = Path(root)/name
            if path.is_symlink():
                raise GitInputError('git_input_cache_unavailable')
        for name in files:
            total += (Path(root)/name).stat().st_size
            if total > limit:
                raise GitInputError('git_input_budget')
    return total


def make_bundle(repository, path, ident, *, base=None):
    from naiyue_test_agent.contracts.git_inputs import input_id
    ref = 'refs/naiyue/input/'+input_id(ident)
    args = ['bundle', 'create', str(Path(path).absolute()), ref]
    if base is not None:
        object_id(base)
        args.append('^'+base)
    repository.run(args)
    return bundle_digest(path, repository.settings.max_bundle_bytes)
