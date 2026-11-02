"""Build one immutable test template from explicit verified local artifacts."""
from email.parser import BytesParser
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
from uuid import uuid4
import zipfile
from contextlib import ExitStack

from packaging.requirements import Requirement
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from naiyue_test_agent.contracts.cloud_lifecycle import ContainerCheck, TemplateReceipt
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.storage.files import (open_store_file, open_store_readonly, check_store_directory,
    sync_store_directory, acquire_store_lock, release_store_lock)
from naiyue_test_agent.storage.cloud.schema import private_file

logger = logging.getLogger(__name__)


class TemplateBuildSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    wheel: Path
    wheel_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    kit_wheel: Path
    kit_profile: Path
    test_lock: Path
    base_image: str = Field(pattern=r'^[a-z0-9][a-z0-9/_.:-]*@sha256:[0-9a-f]{64}$')
    template_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{0,127}$')
    backend_id: str = Field(min_length=1, max_length=128)
    image_tag: str = Field(pattern=r'^[a-z0-9][a-z0-9_.:/-]{0,200}$')
    docker_host: str = Field(pattern=r'^unix:///[^\s]+$')
    docker_executable: str = 'docker'
    package_index: str = Field(pattern=r'^https://[a-zA-Z0-9.-]+(?::[0-9]+)?/[^\s?#@]*$')
    uid: int = Field(gt=0, strict=True)
    gid: int = Field(gt=0, strict=True)
    output: Path
    build_timeout_seconds: float = Field(default=900.0, gt=0, le=3600)


DOCKERFILE = '''ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG TASK_UID
ARG TASK_GID
ARG PACKAGE_INDEX
RUN apt-get update && apt-get install -y --no-install-recommends git bash \\
 && rm -rf /var/lib/apt/lists/*
COPY wheels/runner.lock wheels/test.lock wheels/aitest_kit-*.whl /wheels/
RUN python -m venv /opt/nyt/runner && python -m venv /opt/nyt/test \\
 && /opt/nyt/runner/bin/pip install --no-cache-dir --index-url "$PACKAGE_INDEX" -r /wheels/runner.lock \\
 && /opt/nyt/test/bin/pip install --no-deps /wheels/aitest_kit-*.whl \\
 && /opt/nyt/test/bin/pip install --no-cache-dir --index-url "$PACKAGE_INDEX" -r /wheels/test.lock
COPY wheels/naiyue_test_agent-*.whl /wheels/
RUN /opt/nyt/runner/bin/pip install --no-deps /wheels/naiyue_test_agent-*.whl \\
 && /opt/nyt/runner/bin/python -c "from pathlib import Path;import shutil,naiyue_test_agent;p=Path(naiyue_test_agent.__file__).parent/'_assets/bin';[shutil.copyfile(p/n,Path('/usr/local/bin')/n) for n in ('rg','fd')]" \\
 && chmod 755 /usr/local/bin/rg /usr/local/bin/fd \\
 && /opt/nyt/runner/bin/python -c "from pathlib import Path;import shutil,naiyue_test_agent;shutil.copyfile(Path(naiyue_test_agent.__file__).parent/'cloud_runner/entry.py','/opt/nyt/runner-entry.py')" \\
 && mkdir -p /workspace /state \\
 && chown "$TASK_UID:$TASK_GID" /workspace /state \\
 && rm -rf /wheels
ENV PATH="/opt/nyt/test/bin:/usr/local/bin:/usr/bin:/bin" PYTHONNOUSERSITE=1 HOME=/tmp
USER ${TASK_UID}:${TASK_GID}
WORKDIR /workspace
CMD ["/opt/nyt/runner/bin/python", "-I", "/opt/nyt/runner-entry.py"]
'''


def _read(path):
    check_store_directory(path.absolute().parent)
    with os.fdopen(open_store_readonly(path), 'rb') as stream:
        return stream.read()


def prepare_template(settings, release_id):
    settings.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    check_store_directory(settings.output.absolute().parent)
    lock = settings.output.parent / '.template-prepare.lock'
    try: private_file(lock)
    except FileNotFoundError: pass
    primary, stack = None, ExitStack()
    try:
        fd = open_store_file(lock)
        stack.callback(os.close, fd)
        try: acquire_store_lock(fd)
        except BlockingIOError: raise CloudError('template_prepare_busy') from None
        stack.callback(release_store_lock, fd)
        return _prepare_template(settings, release_id)
    except BaseException as error:
        primary = error
        raise
    finally:
        try: stack.close()
        except OSError as error:
            logger.error('template_prepare_unlock_failed error_type=%s', type(error).__name__)
            if primary is None: raise


def _prepare_template(settings, release_id):
    if settings.output.exists() or settings.output.is_symlink(): raise CloudError('template_output_exists')
    wheel, kit = _read(settings.wheel), _read(settings.kit_wheel)
    profile = json.loads(_read(settings.kit_profile))
    if (set(profile) != {'schema_version', 'aitest_kit'} or profile['schema_version'] != 1
            or set(profile['aitest_kit']) != {'version', 'wheel_sha256'}):
        raise CloudError('template_profile_invalid')
    expected = profile['aitest_kit']
    if hashlib.sha256(wheel).hexdigest() != settings.wheel_sha256 or hashlib.sha256(kit).hexdigest() != expected['wheel_sha256']:
        raise CloudError('template_artifact_mismatch')
    test_lock = _read(settings.test_lock).decode()
    locks = [line.strip() for line in test_lock.splitlines() if line.strip() and not line.startswith('#')]
    if (not all(re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+', line) for line in locks)
            or 'aitest-kit==' + expected['version'] not in locks):
        raise CloudError('template_lock_invalid')
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        metadata = BytesParser().parsebytes(archive.read(next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))))
        native = json.loads(archive.read('naiyue_test_agent/_assets/native.json'))
        if metadata['Name'] != 'naiyue-test-agent' or 'naiyue_test_agent/cloud_runner/preflight.py' not in archive.namelist():
            raise CloudError('template_wheel_invalid')
        requirements = []
        for raw in metadata.get_all('Requires-Dist', []):
            requirement = Requirement(raw)
            if requirement.marker and 'extra' in str(requirement.marker): continue
            if not requirement.specifier or any(s.operator != '==' for s in requirement.specifier):
                raise CloudError('template_runner_not_locked')
            requirements.append(raw)
    docker = [settings.docker_executable, '--host', settings.docker_host]
    environment = {k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','TEMP','SYSTEMROOT','SSL_CERT_FILE','SSL_CERT_DIR')}
    def run(arguments, *, cache_probe=False):
        result = subprocess.run([*docker, *arguments], check=False, capture_output=True, text=True,
            timeout=settings.build_timeout_seconds, env=environment)
        if result.returncode:
            if cache_probe:
                logger.info('cloud_template_cache_probe_failed returncode=%s', result.returncode)
                return None
            logger.error('cloud_template_command_failed phase=%s returncode=%s', arguments[0], result.returncode)
            raise CloudError('template_build_failed')
        return result.stdout
    info = json.loads(run(['info', '--format', '{{json .}}']))
    arch = {'aarch64':'arm64','arm64':'arm64','x86_64':'x86_64','amd64':'x86_64'}.get(info.get('Architecture'))
    if info.get('ID') != settings.backend_id: raise CloudError('backend_identity_mismatch')
    if native.get('target') != 'linux-' + str(arch): raise CloudError('template_architecture_mismatch')
    if run(['image','ls','--filter','reference=' + settings.image_tag,'--format','{{json .}}']).strip():
        raise CloudError('template_tag_exists')
    if run(['image','inspect',settings.base_image], cache_probe=True) is None:
        run(['pull',settings.base_image])
        run(['image','inspect',settings.base_image])
    with tempfile.TemporaryDirectory(prefix='nyt-cloud-template-') as directory:
        context = Path(directory)
        (context/'wheels').mkdir()
        (context/'wheels'/settings.wheel.name).write_bytes(wheel)
        (context/'wheels'/settings.kit_wheel.name).write_bytes(kit)
        (context/'wheels/runner.lock').write_text('\n'.join(requirements)+'\n')
        (context/'wheels/test.lock').write_text(test_lock)
        (context/'Dockerfile').write_text(DOCKERFILE)
        identity = context / 'image-id'
        args = ['build','--network','host','--iidfile',str(identity),'--tag',settings.image_tag,'--build-arg','BASE_IMAGE='+settings.base_image,
            '--build-arg','TASK_UID='+str(settings.uid),'--build-arg','TASK_GID='+str(settings.gid),
            '--build-arg','PACKAGE_INDEX='+settings.package_index]
        for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
            args += ['--build-arg',name+'=']
        run([*args,str(context)])
        image = identity.read_text().strip()
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', image): raise CloudError('template_image_identity_invalid')
    if json.loads(run(['image','inspect',image]))[0]['Id'] != image: raise CloudError('template_image_identity_invalid')
    raw = run(['run','--rm','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--user',f'{settings.uid}:{settings.gid}', image, '/opt/nyt/runner/bin/python','-I','-m',
        'naiyue_test_agent.cloud_runner.preflight','--full'])
    try:
        check = ContainerCheck.model_validate_json(raw).verify(uid=settings.uid, gid=settings.gid,
            kit_version=expected['version'], full=True)
    except (ValidationError, ValueError): raise CloudError('template_preflight_failed') from None
    receipt = TemplateReceipt(template_id=settings.template_id, runner_release_id=release_id,
        image_id=image, base_image=settings.base_image, wheel_sha256=settings.wheel_sha256,
        kit_version=expected['version'], kit_wheel_sha256=expected['wheel_sha256'], uid=settings.uid,
        gid=settings.gid, backend_id=settings.backend_id, toolchain_sha256=check.toolchain_sha256,
        preflight_sha256=digest(check.model_dump()))
    settings.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    check_store_directory(settings.output.absolute().parent)
    fd = open_store_file(settings.output, exclusive_create=True)
    with os.fdopen(fd, 'w') as stream:
        stream.write(receipt.model_dump_json(indent=2)+'\n')
        stream.flush()
        os.fsync(stream.fileno())
    sync_store_directory(settings.output.absolute().parent)
    return receipt
