"""Release archives contain only the promised runtime and checked resources."""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest


def test_checked_download_rejects_wrong_hash(tmp_path, monkeypatch):
    from scripts import native_assets
    monkeypatch.setattr(native_assets.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(b'changed'))
    with pytest.raises(ValueError, match='SHA-256'):
        native_assets.download_checked('https://github.com/vendor/tool/file', '0' * 64, tmp_path)
    assert not list(tmp_path.glob('*.download'))


def test_cache_is_rechecked_before_reuse(tmp_path, monkeypatch):
    from scripts import native_assets
    content = b'expected'
    checksum = hashlib.sha256(content).hexdigest()
    (tmp_path / checksum).write_bytes(b'tampered')
    monkeypatch.setattr(native_assets.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(content))
    result = native_assets.download_checked('https://github.com/vendor/tool/file', checksum, tmp_path)
    assert result.read_bytes() == content


@pytest.mark.parametrize('kind', ['symlink', 'duplicate', 'absent'])
def test_tar_member_must_be_one_regular_expected_file(tmp_path, kind):
    from scripts.native_assets import archive_member
    archive = tmp_path / 'asset.tar.gz'
    with tarfile.open(archive, 'w:gz') as output:
        if kind != 'absent':
            member = tarfile.TarInfo('tool/rg')
            if kind == 'symlink':
                member.type = tarfile.SYMTYPE
                member.linkname = '/untrusted'
                output.addfile(member)
            else:
                member.size = 1
                output.addfile(member, io.BytesIO(b'a'))
                output.addfile(member, io.BytesIO(b'b'))
    with pytest.raises(ValueError, match='member'):
        archive_member(archive, 'tool/rg')


@pytest.mark.parametrize('name', ['../rg', '/rg', 'tool/../../rg'])
def test_archive_paths_are_bounded(tmp_path, name):
    from scripts.native_assets import archive_member
    with pytest.raises(ValueError, match='member'):
        archive_member(tmp_path / 'unused', name)


def test_zip_symlink_cannot_be_packaged_as_a_binary(tmp_path):
    from scripts.native_assets import archive_member
    archive = tmp_path / 'asset.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        member = zipfile.ZipInfo('tool/rg.exe')
        member.external_attr = 0o120777 << 16
        output.writestr(member, '/untrusted')
    with pytest.raises(ValueError, match='member'):
        archive_member(archive, 'tool/rg.exe')


def _wheel(tmp_path, *, extra=None, missing=None):
    name = 'naiyue_test_agent-0.1.0-py3-none-win_amd64.whl'
    prefix = 'naiyue_test_agent-0.1.0.dist-info/'
    files = {
        'naiyue_test_agent/__init__.py': '__version__ = "0.1.0"',
        'naiyue_test_agent/_assets/web/index.html': '<html></html>',
        'naiyue_test_agent/_assets/web/assets/app.js': 'export {};',
        'naiyue_test_agent/_assets/web/assets/app.css': 'body{}',
        'naiyue_test_agent/_assets/bin/rg.exe': 'rg-fixture',
        'naiyue_test_agent/_assets/bin/fd.exe': 'fd-fixture',
        'naiyue_test_agent/_assets/licenses/frontend.json': '{}',
        'naiyue_test_agent/_assets/licenses/rg-LICENSE-MIT': 'MIT',
        'naiyue_test_agent/_assets/licenses/fd-LICENSE-MIT': 'MIT',
        prefix + 'METADATA': 'Metadata-Version: 2.4\nName: naiyue-test-agent\nVersion: 0.1.0\nRequires-Python: >=3.12\n',
        prefix + 'WHEEL': 'Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: py3-none-win_amd64\n',
        prefix + 'entry_points.txt': '[console_scripts]\nnyt = naiyue_test_agent.cli:main\n',
        prefix + 'licenses/LICENSE': 'MIT',
        prefix + 'RECORD': '',
        prefix + 'top_level.txt': 'naiyue_test_agent',
    }
    files['naiyue_test_agent/_assets/native.json'] = json.dumps({
        'target': 'windows-x86_64', 'wheel_tag': 'win_amd64', 'tools': {
            tool: {'filename': tool + '.exe', 'sha256': hashlib.sha256((tool + '-fixture').encode()).hexdigest()}
            for tool in ('rg', 'fd')},
    })
    if missing:
        del files[missing]
    if extra:
        files[extra] = 'not for distribution'
    path = tmp_path / name
    with zipfile.ZipFile(path, 'w') as output:
        for key, value in files.items():
            output.writestr(key, value)
    return path


def test_wheel_whitelist_accepts_only_runtime(tmp_path):
    from scripts.release_artifacts import inspect_wheel
    result = inspect_wheel(_wheel(tmp_path))
    assert result['version'] == '0.1.0'
    assert result['target'] == 'windows-x86_64'


@pytest.mark.parametrize('extra', [
    'tests/test_secret.py', 'docs/handoff.md', '.env/models.json', 'README.md',
    'naiyue_test_agent/_assets/web/source.tsx', 'naiyue_test_agent/credentials.json',
    'naiyue_test_agent/__pycache__/app.pyc', 'naiyue_test_agent/_assets/bin/unused.exe',
    'naiyue_test_agent/_assets/web/assets/source.js.map',
])
def test_wheel_whitelist_rejects_unexpected_files(tmp_path, extra):
    from scripts.release_artifacts import inspect_wheel
    with pytest.raises(ValueError, match='Unexpected'):
        inspect_wheel(_wheel(tmp_path, extra=extra))


@pytest.mark.parametrize('missing', [
    'naiyue_test_agent/_assets/bin/fd.exe',
    'naiyue_test_agent/_assets/web/index.html',
    'naiyue_test_agent-0.1.0.dist-info/licenses/LICENSE',
    'naiyue_test_agent/_assets/licenses/frontend.json',
])
def test_wheel_cannot_omit_required_resources(tmp_path, missing):
    from scripts.release_artifacts import inspect_wheel
    with pytest.raises(ValueError, match='Missing'):
        inspect_wheel(_wheel(tmp_path, missing=missing))


def test_wheel_detects_tampered_native_binary(tmp_path):
    from scripts.release_artifacts import inspect_wheel
    path = _wheel(tmp_path)
    with zipfile.ZipFile(path) as archive:
        content = {name: archive.read(name) for name in archive.namelist()}
    content['naiyue_test_agent/_assets/bin/rg.exe'] = b'tampered'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, value in content.items():
            archive.writestr(name, value)
    with pytest.raises(ValueError, match='SHA-256'):
        inspect_wheel(path)


def test_native_wheel_cannot_claim_universal_compatibility(tmp_path):
    from scripts.release_artifacts import inspect_wheel
    original = _wheel(tmp_path)
    with zipfile.ZipFile(original) as archive:
        content = {name: archive.read(name) for name in archive.namelist()}
    name = 'naiyue_test_agent/_assets/native.json'
    native = json.loads(content[name])
    native['wheel_tag'] = 'any'
    content[name] = json.dumps(native).encode()
    name = 'naiyue_test_agent-0.1.0.dist-info/WHEEL'
    content[name] = content[name].replace(b'win_amd64', b'any')
    path = tmp_path / 'naiyue_test_agent-0.1.0-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, value in content.items():
            archive.writestr(name, value)
    with pytest.raises(ValueError, match='platform'):
        inspect_wheel(path)


def test_release_tag_must_match_single_python_version():
    from scripts.release_artifacts import check_tag
    check_tag('v0.1.0', '0.1.0')
    with pytest.raises(ValueError, match='version'):
        check_tag('v0.2.0', '0.1.0')


def test_staging_ignores_private_and_developer_material(tmp_path):
    from scripts.release_artifacts import stage_python
    source, destination = tmp_path / 'source', tmp_path / 'staging'
    package = source / 'naiyue_test_agent'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('__version__ = "0.1.0"')
    (package / 'secrets.json').write_text('PRIVATE')
    (package / '.env.py').write_text('PRIVATE')
    for name in ('pyproject.toml', 'setup.py', 'README.md', 'LICENSE'):
        (source / name).write_text('fixture')
    (source / 'AGENTS.md').write_text('PRIVATE')
    stage_python(source, destination)
    assert sorted(str(p.relative_to(destination)) for p in destination.rglob('*') if p.is_file()) == [
        'LICENSE', 'README.md', 'naiyue_test_agent/__init__.py', 'pyproject.toml', 'setup.py',
    ]


@pytest.mark.parametrize('linked', ['index', 'assets', 'package', 'license', 'frontend-root'])
def test_frontend_staging_rejects_links_outside_release_sources(tmp_path, linked):
    from scripts.release_artifacts import stage_frontend
    source = tmp_path / 'source'
    frontend = source / 'frontend'
    distribution = frontend / 'dist'
    (distribution / 'assets').mkdir(parents=True)
    (distribution / 'index.html').write_text('<html>safe</html>')
    package = frontend / 'node_modules' / 'example'
    package.mkdir(parents=True)
    (package / 'LICENSE').write_text('MIT')
    (frontend / 'package-lock.json').write_text(json.dumps({'packages': {
        'node_modules/example': {'version': '1.0.0', 'license': 'MIT'}}}))
    outside = tmp_path / 'private'
    outside.mkdir()
    (outside / 'PRIVATE').write_text('PRIVATE-MARKER')
    assets = tmp_path / 'output'
    assets.mkdir()
    if linked == 'index':
        (distribution / 'index.html').unlink()
        (distribution / 'index.html').symlink_to(outside / 'PRIVATE')
    elif linked == 'assets':
        (distribution / 'assets').rmdir()
        (outside / 'app.js').write_text('PRIVATE-MARKER')
        (distribution / 'assets').symlink_to(outside, target_is_directory=True)
    elif linked == 'package':
        (package / 'LICENSE').unlink()
        package.rmdir()
        (outside / 'LICENSE').write_text('PRIVATE-MARKER')
        package.symlink_to(outside, target_is_directory=True)
    elif linked == 'license':
        (package / 'LICENSE').unlink()
        (package / 'LICENSE').symlink_to(outside / 'PRIVATE')
    else:
        frontend.rename(outside / 'frontend')
        frontend.symlink_to(outside / 'frontend', target_is_directory=True)
    with pytest.raises(ValueError, match='link|source'):
        stage_frontend(source, assets)
    assert all(b'PRIVATE-MARKER' not in p.read_bytes() for p in assets.rglob('*') if p.is_file())


def test_staging_metadata_cannot_read_a_symlink(tmp_path):
    from scripts.release_artifacts import stage_python
    source = tmp_path / 'source'
    (source / 'naiyue_test_agent').mkdir(parents=True)
    outside = tmp_path / 'private'
    outside.write_text('PRIVATE-MARKER')
    for name in ('pyproject.toml', 'setup.py', 'README.md', 'LICENSE'):
        (source / name).write_text('fixture')
    (source / 'README.md').unlink()
    (source / 'README.md').symlink_to(outside)
    with pytest.raises(ValueError, match='link|source'):
        stage_python(source, tmp_path / 'staging')


def test_shared_frontend_artifact_keeps_exact_bytes_and_rejects_extra_files(tmp_path):
    from scripts.release_artifacts import stage_built_frontend
    source, destination = tmp_path / 'shared', tmp_path / 'package'
    (source / 'web' / 'assets').mkdir(parents=True)
    (source / 'licenses').mkdir()
    files = {'web/index.html': b'<html>shared</html>', 'web/assets/app.js': b'export {};',
             'web/assets/app.css': b'body{}', 'licenses/frontend.json': b'[]'}
    for name, content in files.items():
        (source / name).write_bytes(content)
    stage_built_frontend(source, destination)
    assert {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob('*') if p.is_file()} == files
    (source / 'PRIVATE.json').write_text('not for release')
    with pytest.raises(ValueError, match='Unexpected'):
        stage_built_frontend(source, tmp_path / 'another')


def test_build_entry_cannot_erase_a_shared_artifact_root_link(monkeypatch, tmp_path):
    from scripts import build_release
    external = tmp_path / 'outside'
    (external / 'web').mkdir(parents=True)
    (external / 'web/index.html').write_text('PRIVATE-MARKER')
    linked = tmp_path / 'linked-artifact'
    linked.symlink_to(external, target_is_directory=True)
    def stage(source, destination):
        destination.mkdir(parents=True)
    def native(target, destination):
        destination.mkdir(parents=True)
        return {'wheel_tag': 'macosx_11_0_arm64'}
    monkeypatch.setattr(build_release, 'stage_python', stage)
    monkeypatch.setattr(build_release, 'prepare_native', native)
    monkeypatch.setattr(build_release, 'source_version', lambda _: '0.1.0')
    monkeypatch.setattr(build_release.subprocess, 'run', lambda *a, **kw: pytest.fail('Linked input reached wheel build'))
    monkeypatch.setattr(sys, 'argv', ['build-release', '--target', 'macos-arm64',
        '--frontend-assets', str(linked), '--output', str(tmp_path / 'wheels')])
    with pytest.raises(ValueError, match='link'):
        build_release.main()


@pytest.mark.parametrize('field', ['hash', 'host', 'version'])
def test_pypi_install_report_must_match_the_tested_artifact(field):
    from scripts.verify_wheel_install import verify_install_report
    info = {'version': '0.1.0', 'filename': 'tested.whl', 'sha256': '0' * 64}
    row = {'metadata': {'name': 'naiyue-test-agent', 'version': '0.1.0'}, 'download_info': {
        'url': 'https://files.pythonhosted.org/tested.whl', 'archive_info': {'hashes': {'sha256': '0' * 64}}}}
    if field == 'hash':
        row['download_info']['archive_info']['hashes']['sha256'] = '1' * 64
    elif field == 'host':
        row['download_info']['url'] = 'https://mirror.invalid/tested.whl'
    else:
        row['metadata']['version'] = '0.2.0'
    with pytest.raises(ValueError, match='install'):
        verify_install_report({'install': [row]}, info, from_pypi=True)


def test_pypi_digest_checks_cannot_be_disabled_by_python_optimization(tmp_path):
    from scripts.release_artifacts import inspect_wheel
    path = _wheel(tmp_path)
    info = inspect_wheel(path)
    (tmp_path / 'expected.json').write_text(json.dumps(info))
    with zipfile.ZipFile(path) as archive:
        content = {name: archive.read(name) for name in archive.namelist()}
    content['naiyue_test_agent/__init__.py'] += b'\n# changed after tests\n'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, value in content.items():
            archive.writestr(name, value)
    program = '''
import hashlib, io, json, sys
from pathlib import Path
from scripts import verify_wheel_install as check
root=Path(sys.argv[1]); info=json.loads((root/'expected.json').read_text())
content=(root/info['filename']).read_bytes()
data={'urls':[{'filename':info['filename'],'digests':{'sha256':hashlib.sha256(content).hexdigest()},'url':'https://files.pythonhosted.org/tested.whl'}]}
check.urllib.request.urlopen=lambda url, **kw: io.BytesIO(json.dumps(data).encode() if url.endswith('/json') else content)
output=root/'download'; output.mkdir()
check.published_file(info,output)
'''
    result = subprocess.run([sys.executable, '-O', '-c', program, str(tmp_path)],
        capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'SHA-256' in result.stderr
