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


@pytest.mark.parametrize('proxy_mode', ['disabled', 'explicit'])
@pytest.mark.parametrize('failed', [False, True, 'timeout'])
@pytest.mark.parametrize('replace_source', [False, True])
def test_receipt_requires_real_preflight_result_and_private_exclusive_output(tmp_path, monkeypatch, failed, replace_source, proxy_mode, caplog):
    settings = build_inputs(tmp_path)
    inherited='http://unselected.invalid:19080'
    selected='http://selected-build.invalid:19081'
    monkeypatch.setenv('HTTP_PROXY',inherited);monkeypatch.setenv('http_proxy',inherited)
    monkeypatch.setenv('NYT_BUILD_PROXY',selected);monkeypatch.setenv('NYT_BUILD_BYPASS','localhost,127.0.0.1')
    if proxy_mode=='explicit':
        settings=TemplateBuildSettings.model_validate(settings.model_dump()|{'build_proxy_env':{
            'HTTP_PROXY':'NYT_BUILD_PROXY','HTTPS_PROXY':'NYT_BUILD_PROXY','NO_PROXY':'NYT_BUILD_BYPASS'}})
    commands, staged = [], {}
    verified = {'runner': settings.wheel.read_bytes(), 'kit': settings.kit_wheel.read_bytes()}
    def run(argv, **kwargs):
        commands.append(argv)
        assert all(v not in (inherited,selected) for v in kwargs['env'].values())
        if 'build' not in argv:assert not any(selected in v for v in argv)
        if 'info' in argv:
            value = {'ID': 'fixture-daemon', 'Architecture': 'aarch64'}
            if replace_source:
                settings.wheel.write_bytes(verified['runner'] + b'changed after verification')
                settings.kit_wheel.write_bytes(b'changed after verification')
        elif 'ls' in argv: return subprocess.CompletedProcess(argv, 0, '', '')
        elif 'build' in argv:
            dockerfile=(Path(argv[-1])/'Dockerfile').read_text()
            assert selected not in dockerfile and not any(('ARG '+n) in dockerfile or ('ENV '+n) in dockerfile for n in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy'))
            staged.update(runner=(Path(argv[-1])/'wheels'/settings.wheel.name).read_bytes(),
                kit=(Path(argv[-1])/'wheels'/settings.kit_wheel.name).read_bytes())
            if failed=='timeout':raise subprocess.TimeoutExpired(argv,900,stderr=selected)
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
        code='template_build_timeout' if failed=='timeout' else 'template_preflight_failed'
        with pytest.raises(CloudError, match=code) as error: prepare_template(settings, 'test-release')
        assert selected not in str(error.value) and selected not in caplog.text
        if failed=='timeout':assert error.value.__suppress_context__
        assert not settings.output.exists()
    else:
        result = prepare_template(settings, 'test-release')
        assert result.kit_version == '0.6.0'
        assert selected not in settings.output.read_text()
        assert json.loads(settings.output.read_text())['image_id'] == result.image_id
        assert settings.output.stat().st_mode & 0o077 == 0
        with pytest.raises(CloudError): prepare_template(settings, 'test-release')
    assert all('--host' in argv and 'unix:///unit/docker.sock' in argv for argv in commands)
    build = next(argv for argv in commands if 'build' in argv)
    assert '--network' in build
    assert not any('pull' in argv for argv in commands)
    assert staged == verified

    arguments=dict(v.split('=',1) for i,v in enumerate(build) if i and build[i-1]=='--build-arg')
    for upper in ('HTTP_PROXY','HTTPS_PROXY','FTP_PROXY','ALL_PROXY','NO_PROXY'):
        expected=(('localhost,127.0.0.1' if upper=='NO_PROXY' else selected)
            if proxy_mode=='explicit' and upper in ('HTTP_PROXY','HTTPS_PROXY','NO_PROXY') else '')
        assert arguments[upper]==arguments[upper.lower()]==expected


@pytest.mark.parametrize('value',[None,'','http://user:password@proxy.invalid:8080','http://proxy.invalid:8080/?token=secret','file:///tmp/proxy','http://proxy.invalid:8080/\n'])
def test_explicit_build_proxy_rejects_missing_or_unsafe_values_before_docker(tmp_path,monkeypatch,value,caplog):
    settings=build_inputs(tmp_path)
    settings=TemplateBuildSettings.model_validate(settings.model_dump()|{'build_proxy_env':{'HTTP_PROXY':'NYT_BUILD_PROXY'}})
    if value is None:monkeypatch.delenv('NYT_BUILD_PROXY',raising=False)
    else:monkeypatch.setenv('NYT_BUILD_PROXY',value)
    def forbidden(*a,**kw):pytest.fail('Docker ran before proxy validation')
    monkeypatch.setattr(subprocess,'run',forbidden)
    with pytest.raises(CloudError,match='template_build_proxy_'):prepare_template(settings,'test-release')
    assert not settings.output.exists()
    assert 'password' not in caplog.text and 'token=secret' not in caplog.text


@pytest.mark.parametrize('mapping',[{'OPENAI_API_KEY':'MODEL_KEY'},{'HTTP_PROXY':'http://proxy.invalid:8080'},{'HTTPS_PROXY':'BAD-NAME'}])
def test_build_proxy_configuration_accepts_only_fixed_keys_and_environment_names(tmp_path,mapping):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TemplateBuildSettings.model_validate(build_inputs(tmp_path).model_dump()|{'build_proxy_env':mapping})


def test_selected_wheel_version_must_match_manifest_before_docker(tmp_path,monkeypatch):
    settings=build_inputs(tmp_path).model_copy(update={'expected_runner_version':'0.5.7'})
    def forbidden(*args,**kwargs):pytest.fail('Docker invoked before wheel version check')
    monkeypatch.setattr(subprocess,'run',forbidden)
    with pytest.raises(CloudError,match='template_wheel_version_mismatch'):prepare_template(settings,'r2')
    assert not settings.output.exists()
