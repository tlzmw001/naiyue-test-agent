"""Explicit staging and wheel allowlist, shared by build and installation gates."""

import argparse
import ast
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import zipfile


PACKAGE = 'naiyue_test_agent'


def release_source(file, boundary):
    """Reject linked files and linked ancestors before reading release input."""
    if boundary.is_symlink() or getattr(boundary, 'is_junction', lambda: False)():
        raise ValueError('Release source boundary must not be a link')
    cursor = boundary
    for part in file.relative_to(boundary).parts:
        cursor = cursor / part
        if cursor.is_symlink() or getattr(cursor, 'is_junction', lambda: False)():
            raise ValueError('Release source must not contain a link')
    if not file.is_file() or not file.resolve().is_relative_to(boundary.resolve()):
        raise ValueError('Release source must be a regular file inside its boundary')
    return file


def source_version(root):
    tree = ast.parse((root / PACKAGE / '__init__.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Missing source version')


def check_tag(tag, version):
    if tag != f'v{version}':
        raise ValueError(f'Tag does not match package version {version}')


def stage_python(source, destination):
    destination.mkdir(parents=True, exist_ok=False)
    for name in ('pyproject.toml', 'setup.py', 'README.md', 'LICENSE'):
        shutil.copyfile(release_source(source / name, source), destination / name)
    for file in (source / PACKAGE).rglob('*.py'):
        relative = file.relative_to(source)
        if any(part.startswith('.') or part == '__pycache__' for part in relative.parts):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(release_source(file, source), target)


def stage_frontend(source, assets):
    frontend = source / 'frontend'
    distribution = frontend / 'dist'
    output = assets / 'web'
    (output / 'assets').mkdir(parents=True)
    shutil.copyfile(release_source(distribution / 'index.html', frontend), output / 'index.html')
    for file in (distribution / 'assets').iterdir():
        if file.is_file() and file.suffix in ('.js', '.css'):
            shutil.copyfile(release_source(file, frontend), output / 'assets' / file.name)
    licenses = assets / 'licenses'
    licenses.mkdir(exist_ok=True)
    lock = json.loads(release_source(frontend / 'package-lock.json', frontend).read_text(encoding='utf-8'))
    entries = []
    for path, package in lock['packages'].items():
        if not path or package.get('dev'):
            continue
        root = frontend / path
        name = path.split('node_modules/')[-1]
        files = []
        for file in root.iterdir():
            if file.is_file() and file.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE')):
                label = re.sub(r'[^a-zA-Z0-9._-]', '_', name) + '-' + file.name
                shutil.copyfile(release_source(file, frontend), licenses / label)
                files.append(label)
        if not files:
            raise ValueError(f'Missing frontend license: {name}')
        entries.append({'name': name, 'version': package['version'], 'license': package.get('license'), 'files': files})
    (licenses / 'frontend.json').write_text(json.dumps(entries, indent=2) + '\n', encoding='utf-8')


def stage_built_frontend(source, destination):
    for file in source.rglob('*'):
        if file.is_dir() and not file.is_symlink():
            continue
        name = file.relative_to(source).as_posix()
        if not (name == 'web/index.html' or re.fullmatch(r'web/assets/[^/]+\.(js|css)', name)
                or re.fullmatch(r'licenses/[a-zA-Z0-9._-]+', name)):
            raise ValueError(f'Unexpected frontend release resource: {name}')
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(release_source(file, source), target)


def inspect_wheel(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Unexpected duplicate wheel entries')
        metadata_names = [name for name in names if name.endswith('.dist-info/METADATA')]
        if len(metadata_names) != 1:
            raise ValueError('Missing package metadata')
        prefix = metadata_names[0].removesuffix('METADATA')
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        version = metadata['Version']
        assets = PACKAGE + '/_assets/'
        required = {PACKAGE + '/__init__.py', assets + 'native.json', assets + 'web/index.html',
            assets + 'licenses/frontend.json', assets + 'licenses/rg-LICENSE-MIT', assets + 'licenses/fd-LICENSE-MIT',
            *(prefix + item for item in ('METADATA', 'WHEEL', 'entry_points.txt', 'RECORD', 'licenses/LICENSE'))}
        missing = required - set(names)
        if missing:
            raise ValueError(f'Missing wheel files: {sorted(missing)}')
        native = json.loads(archive.read(assets + 'native.json'))
        from scripts.native_assets import load_config
        target = load_config()['targets'].get(native['target'])
        if target is None:
            raise ValueError('Unknown wheel platform')
        tag = native['wheel_tag']
        if target['os'] == 'macos':
            valid_tag = re.fullmatch(r'macosx_\d+_\d+_' + target['machine'], tag)
        else:
            valid_tag = tag == target['wheel_tag']
        if not valid_tag:
            raise ValueError('Wrong wheel platform')
        expected_names = {name + ('.exe' if target['os'] == 'windows' else '') for name in ('fd', 'rg')}
        if {tool['filename'] for tool in native['tools'].values()} != expected_names:
            raise ValueError('Wrong native filenames for wheel platform')
        expected_binaries = {assets + 'bin/' + tool['filename'] for tool in native['tools'].values()}
        if set(native['tools']) != {'rg', 'fd'}:
            raise ValueError('Missing native tools')
        if expected_binaries - set(names):
            raise ValueError('Missing bundled executable')
        dist_info = {prefix + item for item in ('METADATA', 'WHEEL', 'entry_points.txt', 'RECORD', 'top_level.txt', 'licenses/LICENSE')}
        for name in names:
            parts = PurePosixPath(name).parts
            code = (name.startswith(PACKAGE + '/') and name.endswith('.py')
                    and not any(p.startswith('.') or p == '__pycache__' for p in parts))
            frontend = bool(re.fullmatch(re.escape(assets) + r'web/assets/[^/]+\.(js|css)', name))
            license_file = bool(re.fullmatch(re.escape(assets) + r'licenses/[a-zA-Z0-9._-]+', name))
            if not (code or frontend or license_file or name in dist_info or name in required or name in expected_binaries):
                raise ValueError(f'Unexpected wheel file: {name}')
            if '..' in parts or name.startswith('/') or '\\' in name:
                raise ValueError('Unexpected wheel path')
        if not any(name.startswith(assets + 'web/assets/') and name.endswith('.js') for name in names):
            raise ValueError('Missing frontend JavaScript')
        for tool in native['tools'].values():
            if hashlib.sha256(archive.read(assets + 'bin/' + tool['filename'])).hexdigest() != tool['sha256']:
                raise ValueError('Bundled executable SHA-256 mismatch')
        expected_name = f'{PACKAGE}-{version}-py3-none-{native["wheel_tag"]}.whl'
        if (path.name != expected_name or metadata['Name'] != 'naiyue-test-agent'
                or prefix != f'{PACKAGE}-{version}.dist-info/'):
            raise ValueError('Wheel filename or metadata version mismatch')
        wheel = BytesParser().parsebytes(archive.read(prefix + 'WHEEL'))
        if wheel['Root-Is-Purelib'] != 'false' or wheel['Tag'] != f'py3-none-{native["wheel_tag"]}':
            raise ValueError('Wrong wheel platform tag')
        if b'nyt = naiyue_test_agent.cli:main' not in archive.read(prefix + 'entry_points.txt'):
            raise ValueError('Missing nyt console script')
        application = hashlib.sha256()
        for name in sorted(names):
            if name.endswith('.py') or name.startswith(assets + 'web/'):
                content = archive.read(name)
                if name.endswith('.py'):
                    content = content.replace(b'\r\n', b'\n')
                application.update(name.encode() + b'\0' + hashlib.sha256(content).digest())
        return {'filename': path.name, 'version': version, 'target': native['target'],
                'files': len(names), 'bytes': path.stat().st_size,
                'application_sha256': application.hexdigest(),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def inspect_release(wheels, version):
    from scripts.native_assets import load_config
    reports = [inspect_wheel(path) for path in wheels]
    targets = set(load_config()['targets'])
    if len(reports) != len(targets) or {item['target'] for item in reports} != targets:
        raise ValueError(f'A release requires exactly {len(targets)} distinct platform wheels')
    if any(item['version'] != version for item in reports):
        raise ValueError('Release artifact version mismatch')
    if len({item['application_sha256'] for item in reports}) != 1:
        raise ValueError('Release wheels were built from different application source snapshots')
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheels', type=Path, nargs='*')
    parser.add_argument('--tag')
    parser.add_argument('--complete-release', action='store_true')
    args = parser.parse_args()
    if args.tag:
        check_tag(args.tag, source_version(Path(__file__).resolve().parents[1]))
    reports = (inspect_release(args.wheels, source_version(Path(__file__).resolve().parents[1]))
               if args.complete_release else [inspect_wheel(path) for path in args.wheels])
    for report in reports:
        print(json.dumps(report))


if __name__ == '__main__':
    main()
