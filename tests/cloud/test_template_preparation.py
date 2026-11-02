import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from naiyue_test_agent.cloud_install.template import TemplateBuildSettings, prepare_template
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from lifecycle_helpers import template


def build_inputs(tmp_path):
    wheel = tmp_path / 'naiyue_test_agent-0.5.6-py3-none-manylinux_2_17_aarch64.whl'
    with zipfile.ZipFile(wheel, 'w') as z:
        z.writestr('naiyue_test_agent-0.5.6.dist-info/METADATA', 'Name: naiyue-test-agent\nVersion: 0.5.6\nRequires-Dist: pydantic==2.13.5\n')
        z.writestr('naiyue_test_agent/_assets/native.json', json.dumps({'target': 'linux-arm64'}))
        z.writestr('naiyue_test_agent/cloud_runner/preflight.py', '# Unit-only artifact fixture, never executed\n')
    kit = tmp_path / 'aitest_kit-0.6.0-py3-none-any.whl'
    kit.write_bytes(b'unit-only verified artifact fixture')
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'schema_version': 1, 'aitest_kit': {'version': '0.6.0',
        'wheel_sha256': hashlib.sha256(kit.read_bytes()).hexdigest()}}))
    lock = tmp_path / 'test.lock'
    lock.write_text('aitest-kit==0.6.0\npytest==9.1.1\n')
    return TemplateBuildSettings(wheel=wheel, wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
        kit_wheel=kit, kit_profile=profile, test_lock=lock, base_image='python@sha256:' + '2' * 64,
        template_id='python-tests', backend_id='fixture-daemon', image_tag='nyt-cloud:unit-only',
        docker_host='unix:///unit/docker.sock', package_index='https://pypi.org/simple',
        uid=template().uid, gid=template().gid, output=tmp_path / 'receipt.json')


def test_template_build_rejects_hash_mismatch_before_docker(tmp_path, monkeypatch):
    settings = build_inputs(tmp_path)
    settings.wheel.write_bytes(b'changed')
    def forbidden(*a, **kw): pytest.fail('Docker invoked before artifact verification')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    with pytest.raises(CloudError, match='template_artifact_mismatch'):
        prepare_template(settings, 'test-release')
    assert not settings.output.exists()


@pytest.mark.parametrize('failed', [False, True])
@pytest.mark.parametrize('replace_source', [False, True])
def test_receipt_requires_real_preflight_result_and_private_exclusive_output(tmp_path, monkeypatch, failed, replace_source):
    settings = build_inputs(tmp_path)
    commands, staged = [], {}
    verified = {'runner': settings.wheel.read_bytes(), 'kit': settings.kit_wheel.read_bytes()}
    def run(argv, **kwargs):
        commands.append(argv)
        if 'info' in argv:
            value = {'ID': 'fixture-daemon', 'Architecture': 'aarch64'}
            if replace_source:
                settings.wheel.write_bytes(verified['runner'] + b'changed after verification')
                settings.kit_wheel.write_bytes(b'changed after verification')
        elif 'ls' in argv: return subprocess.CompletedProcess(argv, 0, '', '')
        elif 'build' in argv:
            staged.update(runner=(Path(argv[-1])/'wheels'/settings.wheel.name).read_bytes(),
                kit=(Path(argv[-1])/'wheels'/settings.kit_wheel.name).read_bytes())
            Path(argv[argv.index('--iidfile') + 1]).write_text('sha256:' + '1' * 64)
            value = {}
        elif 'inspect' in argv: value = [{'Id': 'sha256:' + '1' * 64}]
        elif 'run' in argv:
            value = {'schema_version': 1, 'uid': settings.uid, 'euid': settings.uid, 'gid': settings.gid,
                'egid': settings.gid, 'groups': [settings.gid], 'runner_version': '0.5.6', 'no_new_privs': 1,
                'caps': dict.fromkeys(('CapEff','CapPrm','CapInh','CapAmb','CapBnd'),0),
                'runner_readonly': True, 'kit_version': '0.6.0', 'toolchain_sha256': '4' * 64,
                'checks': {name:'passed' for name in ('kit_cli','doctor','pytest','report','pip_check','native_tools')}}
            if failed: value['checks']['pytest'] = 'failed'
        else: value = {}
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), '')
    monkeypatch.setattr(subprocess, 'run', run)
    if failed:
        with pytest.raises(CloudError, match='template_preflight_failed'): prepare_template(settings, 'test-release')
        assert not settings.output.exists()
    else:
        result = prepare_template(settings, 'test-release')
        assert result.kit_version == '0.6.0'
        assert json.loads(settings.output.read_text())['image_id'] == result.image_id
        assert settings.output.stat().st_mode & 0o077 == 0
        with pytest.raises(CloudError): prepare_template(settings, 'test-release')
    assert all('--host' in argv and 'unix:///unit/docker.sock' in argv for argv in commands)
    build = next(argv for argv in commands if 'build' in argv)
    assert '--network' in build
    assert not any('pull' in argv for argv in commands)
    assert staged == verified
