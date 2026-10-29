"""Execution wheels have a separate, strict source/dependency/resource boundary."""
import hashlib
import json
from pathlib import Path
import shutil
import stat
import sys
import tomllib
import zipfile

import pytest

from scripts import envd_artifacts as runtime
from scripts.native_assets import load_config


def _wheel(tmp_path, target='linux-x86_64'):
    staging = tmp_path / 'stage'
    runtime.stage_envd(runtime.ROOT, staging, target)
    assets = staging / 'naiyue_test_agent/_assets'
    native = {'target': target, 'wheel_tag': load_config()['targets'][target]['wheel_tag'], 'tools': {}}
    for name, values in load_config()['tools'].items():
        binary = assets / 'bin' / name
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes((name + '-fixture').encode())
        native['tools'][name] = {'filename': name, 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
        for license in values['licenses']:
            path = assets / 'licenses' / (name + '-' + license)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('license fixture')
    (assets / 'native.json').write_text(json.dumps(native))
    config = tomllib.loads((staging / 'pyproject.toml').read_text())['project']
    version = config['version']
    prefix = 'naiyue_test_agent_envd-' + version + '.dist-info/'
    content = {p.relative_to(staging).as_posix(): p.read_bytes()
        for p in (staging / 'naiyue_test_agent').rglob('*') if p.is_file()}
    content.update({
        prefix + 'METADATA': ('Metadata-Version: 2.4\nName: naiyue-test-agent-envd\nVersion: ' + version +
            '\nRequires-Python: ' + config['requires-python'] + '\n' +
            ''.join('Requires-Dist: ' + item + '\n' for item in config['dependencies'])).encode(),
        prefix + 'WHEEL': ('Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: py3-none-' + native['wheel_tag'] + '\n').encode(),
        prefix + 'licenses/LICENSE': (staging / 'LICENSE').read_bytes(),
        prefix + 'RECORD': b'',
        prefix + 'top_level.txt': b'naiyue_test_agent\n',
    })
    path = tmp_path / ('naiyue_test_agent_envd-' + version + '-py3-none-' + native['wheel_tag'] + '.whl')
    with zipfile.ZipFile(path, 'w') as archive:
        for name, value in content.items():
            archive.writestr(name, value)
    return path


def _change(path, name, value):
    with zipfile.ZipFile(path) as archive:
        content = {item: archive.read(item) for item in archive.namelist()}
    if value is None:
        del content[name]
    else:
        content[name] = value
    with zipfile.ZipFile(path, 'w') as archive:
        for item, data in content.items():
            archive.writestr(item, data)


def test_stage_uses_only_execution_sources_and_root_locked_dependencies(tmp_path):
    target = tmp_path / 'stage'
    runtime.stage_envd(runtime.ROOT, target, 'linux-x86_64')
    profile = runtime.load_profile()
    assert len(profile['files']) == 65
    assert {'naiyue_test_agent/envd/'+name+'.py' for name in ('workspace','file_mutations','identity','attach','framing','resources')} <= set(profile['files'])
    assert 'naiyue_test_agent/diagnostics.py' in profile['files']
    files = {p.relative_to(target).as_posix() for p in (target / 'naiyue_test_agent').rglob('*.py')}
    assert files == set(profile['files'])
    for name in files:
        assert (target / name).read_bytes() == runtime.execution_source(runtime.ROOT, name, profile['version'])
    project = tomllib.loads((target / 'pyproject.toml').read_text())['project']
    original = tomllib.loads((runtime.ROOT / 'pyproject.toml').read_text())['project']
    assert project['name'] == 'naiyue-test-agent-envd'
    assert len(project['dependencies']) == 45
    assert set(project['dependencies']) <= set(original['dependencies'])
    assert not project.get('scripts')
    assert not (target / 'naiyue_test_agent/cli').exists()
    assert not (target / 'naiyue_test_agent/_assets/web').exists()


def test_execution_version_is_independent_of_controller_source_version(tmp_path):
    root=tmp_path/'source'
    profile=runtime.load_profile()
    for name in [*profile['files'],'pyproject.toml','setup.py','LICENSE','release/envd.json']:
        source=runtime.ROOT/name
        target=root/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    (root/'naiyue_test_agent/__init__.py').write_text("__version__ = '9.9.9'\n")
    staged=tmp_path/'staged'
    runtime.stage_envd(root,staged,'linux-x86_64')
    project=tomllib.loads((staged/'pyproject.toml').read_text())['project']
    assert project['version']==profile['version']
    assert (staged/'naiyue_test_agent/__init__.py').read_text().strip().endswith(repr(profile['version']))
    manifest=json.loads((staged/'naiyue_test_agent/_assets/envd.json').read_text())
    assert manifest['package_version']==profile['version']


def test_execution_stage_does_not_silently_drop_new_package_root_logic(tmp_path):
    root=tmp_path/'source'
    profile=runtime.load_profile()
    for name in [*profile['files'],'pyproject.toml','setup.py','LICENSE','release/envd.json']:
        source=runtime.ROOT/name
        target=root/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    target=root/'naiyue_test_agent/__init__.py'
    target.write_text(target.read_text()+'\nNEW_RUNTIME_BEHAVIOR = True\n')
    with pytest.raises(ValueError,match='unsupported controller source'):
        runtime.stage_envd(root,tmp_path/'staged','linux-x86_64')


@pytest.mark.parametrize('changed', ['source', 'dependency'])
def test_execution_build_requires_new_version_for_changed_content(tmp_path,changed):
    root=tmp_path/'source'
    profile=runtime.load_profile()
    for name in [*profile['files'],'pyproject.toml','setup.py','LICENSE','release/envd.json']:
        source=runtime.ROOT/name
        target=root/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    if changed=='source':
        target=root/'naiyue_test_agent/execution/git_snapshot.py'
        target.write_text(target.read_text()+'\n# changed execution source\n')
    else:
        target=root/'pyproject.toml'
        target.write_text(target.read_text().replace('Pillow==12.3.0','Pillow==12.3.1'))
    with pytest.raises(ValueError,match='changed without an execution version update'):
        runtime.stage_envd(root,tmp_path/'staged','linux-x86_64')


def test_execution_inspector_rejects_profile_digest_drift(tmp_path,monkeypatch):
    path=_wheel(tmp_path)
    profile=runtime.load_profile()
    monkeypatch.setattr(runtime,'load_profile',lambda root=runtime.ROOT:{**profile,'execution_sha256':'0'*64})
    with pytest.raises(ValueError,match='changed without an execution version update'):
        runtime.inspect_envd_wheel(path)


@pytest.mark.parametrize('target', ['linux-x86_64', 'linux-arm64'])
def test_execution_wheel_has_its_own_strict_inspector(tmp_path, target):
    path = _wheel(tmp_path, target)
    result = runtime.inspect_envd_wheel(path)
    assert result['target'] == target
    assert len(result['dependencies']) == 45
    assert len(result['execution_sha256']) == 64
    assert result['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    from scripts.release_artifacts import inspect_wheel
    with pytest.raises(ValueError):
        inspect_wheel(path)


@pytest.mark.parametrize('extra', [
    'naiyue_test_agent/application/service.py', 'naiyue_test_agent/agent_runtime/__init__.py',
    'naiyue_test_agent/model_access/__init__.py', 'naiyue_test_agent/cli/__init__.py',
    'naiyue_test_agent/mcp_client/__init__.py', 'naiyue_test_agent/_assets/web/index.html',
    'models.json', '../outside.py', '/absolute.py',
])
def test_execution_wheel_rejects_unrelated_or_unsafe_content(tmp_path, extra):
    path = _wheel(tmp_path)
    _change(path, extra, b'not execution content')
    with pytest.raises(ValueError):
        runtime.inspect_envd_wheel(path)


@pytest.mark.parametrize('name,content', [
    ('naiyue_test_agent/envd/processes.py', b'# changed'),
    ('naiyue_test_agent/_assets/bin/rg', b'tampered'),
    ('naiyue_test_agent/_assets/licenses/fd-LICENSE-MIT', None),
    ('naiyue_test_agent/envd/protocol.py', None),
])
def test_execution_wheel_rejects_tampering_and_missing_resources(tmp_path, name, content):
    path = _wheel(tmp_path)
    _change(path, name, content)
    with pytest.raises(ValueError):
        runtime.inspect_envd_wheel(path)


def test_rehashing_a_modified_source_cannot_claim_the_current_execution_build(tmp_path):
    path = _wheel(tmp_path)
    name = 'naiyue_test_agent/envd/processes.py'
    value = b'# replaced implementation\n'
    _change(path, name, value)
    manifest_path = 'naiyue_test_agent/_assets/envd.json'
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read(manifest_path))
    manifest['files'][name] = hashlib.sha256(value).hexdigest()
    manifest['execution_sha256'] = runtime.execution_digest(manifest['files'])
    _change(path, manifest_path, json.dumps(manifest).encode())
    with pytest.raises(ValueError, match='source'):
        runtime.inspect_envd_wheel(path)


def test_distribution_metadata_cannot_claim_a_different_execution_version(tmp_path):
    path = _wheel(tmp_path)
    with zipfile.ZipFile(path) as archive:
        content = {name: archive.read(name) for name in archive.namelist()}
    metadata = next(name for name in content if name.endswith('.dist-info/METADATA'))
    version = runtime.load_profile()['version']
    claimed = version + '.99'
    content[metadata] = content[metadata].replace(('Version: ' + version + '\n').encode(), ('Version: ' + claimed + '\n').encode())
    manifest_path = 'naiyue_test_agent/_assets/envd.json'
    manifest = json.loads(content[manifest_path])
    manifest['package_version'] = claimed
    content[manifest_path] = json.dumps(manifest).encode()
    prefix = metadata.removesuffix('METADATA')
    changed_prefix = prefix.replace('-' + version + '.', '-' + claimed + '.')
    path = path.rename(path.with_name(path.name.replace('-' + version + '-', '-' + claimed + '-')))
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in content.items():
            archive.writestr(changed_prefix + name[len(prefix):] if name.startswith(prefix) else name, data)
    with pytest.raises(ValueError, match='version'):
        runtime.inspect_envd_wheel(path)


@pytest.mark.parametrize('change', ['provider', 'unlocked', 'missing', 'entrypoint', 'symlink', 'duplicate'])
def test_execution_metadata_and_zip_boundary_cannot_be_relaxed(tmp_path, change):
    path = _wheel(tmp_path)
    with zipfile.ZipFile(path) as archive:
        metadata = next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))
        content = archive.read(metadata)
    if change == 'provider':
        _change(path, metadata, content + b'Requires-Dist: openai==3.19.2\n')
    elif change == 'unlocked':
        _change(path, metadata, content.replace(b'Pillow==12.3.0', b'Pillow>=12'))
    elif change == 'missing':
        _change(path, metadata, content.replace(b'Requires-Dist: Pillow==12.3.0\n', b''))
    elif change == 'entrypoint':
        _change(path, metadata.removesuffix('METADATA') + 'entry_points.txt', b'[console_scripts]\nnyt = naiyue_test_agent.cli:main\n')
    elif change == 'symlink':
        with zipfile.ZipFile(path, 'a') as archive:
            info = zipfile.ZipInfo('linked')
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, '/outside')
    else:
        with zipfile.ZipFile(path, 'a') as archive:
            archive.writestr(metadata, content)
    with pytest.raises(ValueError):
        runtime.inspect_envd_wheel(path)


def test_source_symlinks_cannot_enter_execution_staging(tmp_path):
    source = tmp_path / 'source'
    source.symlink_to(runtime.ROOT, target_is_directory=True)
    with pytest.raises(ValueError, match='link'):
        runtime.stage_envd(source, tmp_path / 'stage', 'linux-x86_64')


@pytest.mark.parametrize('target,daemon_arch,accepted', [
    ('linux-x86_64', 'x86_64', True), ('linux-x86_64', 'amd64', True),
    ('linux-arm64', 'aarch64', True), ('linux-arm64', 'arm64', True),
    ('linux-x86_64', 'aarch64', False), ('linux-arm64', 'amd64', False),
])
def test_sandbox_build_checks_real_linux_wheel_architecture(tmp_path, monkeypatch, target, daemon_arch, accepted):
    from scripts import build_sandbox_image
    wheel = _wheel(tmp_path, target)
    assert runtime.inspect_envd_wheel(wheel)['target'] == target
    mutations = []
    kit_bytes = b'fixture aitest 0.6.0 wheel'
    kit_sha = hashlib.sha256(kit_bytes).hexdigest()
    monkeypatch.setattr(build_sandbox_image, 'load_image_profile',
        lambda: {'aitest_kit': {'version': '0.6.0', 'wheel_sha256': kit_sha}})

    def query(argv):
        assert argv[:3] == ['docker', '--context', 'fixture-context']
        if argv[3] == 'info':
            return json.dumps({'Architecture': daemon_arch, 'ID': 'fixture-daemon'}).encode()
        assert argv[3:5] == ['image', 'inspect']
        return json.dumps([{'Id': 'fixture-' + argv[-1], 'RepoDigests': ['fixture@sha256:base']}]).encode()

    def mutate(argv, *, check):
        assert check is True
        if argv[:4] == [sys.executable, '-m', 'pip', 'download']:
            (Path(argv[argv.index('--dest') + 1]) / 'aitest_kit-0.6.0-py3-none-any.whl').write_bytes(kit_bytes)
        elif argv[3] == 'build':
            stage = Path(argv[-1])
            assert (stage / 'envd.whl').read_bytes() == wheel.read_bytes()
            assert (stage / 'aitest.whl').read_bytes() == kit_bytes
            assert (stage / 'Dockerfile').read_bytes() == (Path(build_sandbox_image.__file__).parents[1] / 'sandbox/Dockerfile').read_bytes()
        mutations.append(argv)

    monkeypatch.setattr(build_sandbox_image.subprocess, 'check_output', query)
    monkeypatch.setattr(build_sandbox_image.subprocess, 'run', mutate)
    output = tmp_path / 'build.json'
    monkeypatch.setattr(sys, 'argv', ['build_sandbox_image', '--wheel-dir', str(tmp_path),
        '--base-image', 'fixture:base', '--tag', 'fixture:result', '--context', 'fixture-context', '--output', str(output)])
    if not accepted:
        with pytest.raises(ValueError, match='architecture'):
            build_sandbox_image.main()
        assert mutations == [] and not output.exists()
        return
    build_sandbox_image.main()
    assert [argv[3] for argv in mutations] == ['download', 'pull', 'build']
    report = json.loads(output.read_text())
    assert report['wheel']['target'] == target
    assert report['image_id'] == 'fixture-fixture:result'
    assert report['daemon_id'] == 'fixture-daemon'


def test_sandbox_image_installs_pinned_aitest_kit_wheel(tmp_path, monkeypatch):
    from scripts import build_sandbox_image
    wheel = _wheel(tmp_path)
    kit_name = 'aitest_kit-0.6.0-py3-none-any.whl'
    kit_bytes = b'fixture aitest 0.6.0 wheel'
    kit_sha = hashlib.sha256(kit_bytes).hexdigest()
    monkeypatch.setattr(build_sandbox_image, 'load_image_profile',
        lambda: {'aitest_kit': {'version': '0.6.0', 'wheel_sha256': kit_sha}})
    commands = []

    def query(argv):
        if argv[3] == 'info':
            return json.dumps({'Architecture': 'amd64', 'ID': 'fixture-daemon'}).encode()
        return json.dumps([{'Id': 'fixture-image', 'RepoDigests': ['fixture@sha256:base']}]).encode()

    def run(argv, *, check):
        assert check is True
        commands.append(argv)
        if argv[:4] == [sys.executable, '-m', 'pip', 'download']:
            (Path(argv[argv.index('--dest') + 1]) / kit_name).write_bytes(kit_bytes)
        if argv[3:4] == ['build']:
            stage = Path(argv[-1])
            assert (stage / 'aitest.whl').read_bytes() == kit_bytes
            assert 'aitest-kit' in (stage / 'Dockerfile').read_text()

    monkeypatch.setattr(build_sandbox_image.subprocess, 'check_output', query)
    monkeypatch.setattr(build_sandbox_image.subprocess, 'run', run)
    output = tmp_path / 'image.json'
    monkeypatch.setattr(sys, 'argv', ['build_sandbox_image', '--wheel-dir', str(tmp_path),
        '--base-image', 'fixture:base', '--tag', 'fixture:result', '--context', 'fixture-context',
        '--output', str(output)])
    build_sandbox_image.main()
    assert [argv[3] for argv in commands if argv[0] == 'docker'] == ['pull', 'build']
    assert any(argv[:4] == [sys.executable, '-m', 'pip', 'download'] for argv in commands)
    assert json.loads(output.read_text())['aitest_kit'] == {'version': '0.6.0', 'wheel_sha256': kit_sha}


def test_sandbox_image_rejects_wrong_aitest_kit_hash_before_docker_pull(tmp_path, monkeypatch):
    from scripts import build_sandbox_image
    _wheel(tmp_path)
    monkeypatch.setattr(build_sandbox_image, 'load_image_profile',
        lambda: {'aitest_kit': {'version': '0.6.0', 'wheel_sha256': '0' * 64}})
    docker_calls = []

    def query(argv):
        docker_calls.append(argv)
        if argv[3] == 'info':
            return json.dumps({'Architecture': 'amd64', 'ID': 'fixture-daemon'}).encode()
        raise AssertionError('Docker image must not be accessed for a bad Kit wheel')

    def run(argv, *, check):
        if argv[:4] == [sys.executable, '-m', 'pip', 'download']:
            (Path(argv[argv.index('--dest') + 1]) / 'aitest_kit-0.6.0-py3-none-any.whl').write_bytes(b'wrong wheel')
            return
        raise AssertionError('Docker pull/build must not run for a bad Kit wheel')

    monkeypatch.setattr(build_sandbox_image.subprocess, 'check_output', query)
    monkeypatch.setattr(build_sandbox_image.subprocess, 'run', run)
    monkeypatch.setattr(sys, 'argv', ['build_sandbox_image', '--wheel-dir', str(tmp_path),
        '--base-image', 'fixture:base', '--tag', 'fixture:result', '--context', 'fixture-context',
        '--output', str(tmp_path / 'image.json')])
    with pytest.raises(ValueError, match='AITest Kit wheel SHA-256'):
        build_sandbox_image.main()
    assert [argv[3] for argv in docker_calls] == ['info']


def test_image_builder_rejects_the_full_agent_before_docker_io(tmp_path, monkeypatch):
    from scripts import build_sandbox_image
    from tests.test_release_artifacts import _wheel as application_wheel
    wheel = application_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        content = {name: archive.read(name) for name in archive.namelist()}
    native_path = 'naiyue_test_agent/_assets/native.json'
    native = json.loads(content[native_path])
    tag = load_config()['targets']['linux-x86_64']['wheel_tag']
    native.update(target='linux-x86_64', wheel_tag=tag)
    for name, detail in native['tools'].items():
        content['naiyue_test_agent/_assets/bin/' + name] = content.pop(
            'naiyue_test_agent/_assets/bin/' + detail['filename'])
        detail['filename'] = name
    content[native_path] = json.dumps(native).encode()
    metadata = 'naiyue_test_agent-0.1.0.dist-info/WHEEL'
    content[metadata] = content[metadata].replace(b'win_amd64', tag.encode())
    wheel = wheel.rename(wheel.with_name(wheel.name.replace('win_amd64', tag)))
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, value in content.items():
            archive.writestr(name, value)
    calls = []
    def query(argv):
        calls.append(argv)
        raise AssertionError('Full application wheel must not contact Docker')
    monkeypatch.setattr(build_sandbox_image.subprocess, 'check_output', query)
    monkeypatch.setattr(sys, 'argv', ['build_sandbox_image', '--wheel-dir', str(tmp_path),
        '--base-image', 'fixture:base', '--tag', 'fixture:result', '--context', 'fixture-context',
        '--output', str(tmp_path / 'build.json')])
    with pytest.raises(ValueError, match='execution'):
        build_sandbox_image.main()
    assert calls == []
