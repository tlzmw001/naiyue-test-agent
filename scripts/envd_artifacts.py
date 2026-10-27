"""An explicit execution-only artifact, independent of the full application's gate."""
import ast
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tomllib
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from scripts.native_assets import ROOT, load_config
from scripts.release_artifacts import release_source

PACKAGE = 'naiyue_test_agent'
DISTRIBUTION = 'naiyue-test-agent-envd'


def load_profile(root=ROOT):
    profile = json.loads(release_source(root / 'release/envd.json', root).read_text())
    if (set(profile) != {'schema_version', 'version', 'execution_sha256', 'dependencies_sha256', 'files', 'dependencies'}
            or profile['schema_version'] != 2):
        raise ValueError('Invalid execution profile')
    if not isinstance(profile['version'], str) or re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', profile['version']) is None:
        raise ValueError('Invalid execution package version')
    if any(not isinstance(profile[name],str) or re.fullmatch(r'[0-9a-f]{64}',profile[name]) is None
            for name in ('execution_sha256','dependencies_sha256')):
        raise ValueError('Invalid execution profile digest')
    files = profile['files']
    if not isinstance(files, list) or len(files) != len(set(files)):
        raise ValueError('Execution files must be a unique list')
    for name in files:
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or '\\' in name or
                not name.startswith(PACKAGE + '/') or not name.endswith('.py')):
            raise ValueError('Invalid execution source path')
        if any(part in path.parts for part in ('application', 'agent_runtime', 'model_access', 'cli', 'web', 'mcp_client')):
            raise ValueError('Controller source is not an execution dependency')
    dependencies = profile['dependencies']
    if (not isinstance(dependencies, list) or len(dependencies) != len(set(dependencies)) or
            any(name != canonicalize_name(name) for name in dependencies)):
        raise ValueError('Execution dependencies must use unique canonical names')
    return profile


def dependency_pins(root=ROOT, profile=None):
    profile = profile or load_profile(root)
    project = tomllib.loads(release_source(root / 'pyproject.toml', root).read_text())['project']
    locked = {}
    for text in project['dependencies']:
        requirement = Requirement(text)
        locked[canonicalize_name(requirement.name)] = text
    result = []
    for name in profile['dependencies']:
        if name not in locked:
            raise ValueError('Execution dependency is absent from application lock: ' + name)
        text = locked[name]
        requirement = Requirement(text)
        rules = list(requirement.specifier)
        if (requirement.marker or requirement.url or requirement.extras or len(rules) != 1 or
                rules[0].operator != '==' or '*' in rules[0].version):
            raise ValueError('Execution dependencies must remain exactly pinned')
        result.append(text)
    return result


def execution_digest(files):
    digest = hashlib.sha256()
    for name, value in sorted(files.items()):
        digest.update(name.encode() + b'\0' + bytes.fromhex(value))
    return digest.hexdigest()


def protocol_version(root=ROOT):
    source = release_source(root / PACKAGE / 'contracts/sandbox.py', root)
    for node in ast.parse(source.read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(item, ast.Name) and item.id == 'PROTOCOL_VERSION' for item in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Missing execution protocol version')


def execution_source(root, name, version):
    source = release_source(root / name, root)
    if name == PACKAGE + '/__init__.py':
        nodes = [node for node in ast.parse(source.read_text()).body
            if not (isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str))]
        if (len(nodes) != 1 or not isinstance(nodes[0],ast.Assign) or len(nodes[0].targets) != 1
                or not isinstance(nodes[0].targets[0],ast.Name) or nodes[0].targets[0].id != '__version__'
                or not isinstance(nodes[0].value,ast.Constant) or not isinstance(nodes[0].value.value,str)):
            raise ValueError('Execution package root contains unsupported controller source')
        return ('"""Naiyue Test Agent application modules."""\n\n__version__ = ' + repr(version) + '\n').encode()
    return source.read_bytes()


def stage_envd(root, destination, target):
    root, destination = Path(root), Path(destination)
    profile = load_profile(root)
    if target not in load_config()['targets'] or load_config()['targets'][target]['os'] != 'linux':
        raise ValueError('Execution runtime requires a Linux release target')
    pins = dependency_pins(root, profile)
    destination.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name in profile['files']:
        output = destination / name
        output.parent.mkdir(parents=True, exist_ok=True)
        content = execution_source(root, name, profile['version'])
        output.write_bytes(content)
        hashes[name] = hashlib.sha256(content).hexdigest()
    if (execution_digest(hashes) != profile['execution_sha256'] or
            hashlib.sha256(json.dumps(pins,separators=(',',':')).encode()).hexdigest() != profile['dependencies_sha256']):
        raise ValueError('Execution source or dependencies changed without an execution version update')
    for name in ('setup.py', 'LICENSE'):
        shutil.copyfile(release_source(root / name, root), destination / name)
    settings = tomllib.loads(release_source(root / 'pyproject.toml', root).read_text())
    quote = json.dumps
    metadata = [
        '[build-system]', 'requires = ' + quote(settings['build-system']['requires']),
        'build-backend = ' + quote(settings['build-system']['build-backend']), '', '[project]',
        'name = ' + quote(DISTRIBUTION), 'version = ' + quote(profile['version']),
        'description = "Container-local file, process and Git execution runtime"',
        'readme = { text = "Execution service for Naiyue sandboxes. No controller or model provider.", content-type = "text/plain" }',
        'requires-python = ' + quote(settings['project']['requires-python']),
        'license = ' + quote(settings['project']['license']), 'license-files = ["LICENSE"]',
        'dependencies = ' + quote(pins), '', '[tool.setuptools]', 'include-package-data = false',
        '', '[tool.setuptools.packages.find]', 'include = ["naiyue_test_agent", "naiyue_test_agent.*"]', 'namespaces = false',
        '', '[tool.setuptools.package-data]',
        'naiyue_test_agent = ["_assets/envd.json", "_assets/native.json", "_assets/bin/rg", "_assets/bin/fd", "_assets/licenses/*"]',
    ]
    (destination / 'pyproject.toml').write_text('\n'.join(metadata) + '\n')
    assets = destination / PACKAGE / '_assets'
    assets.mkdir(exist_ok=True)
    manifest = dict(schema_version=1, package_version=profile['version'], protocol_version=protocol_version(root),
        target=target, files=hashes, dependencies=pins, execution_sha256=execution_digest(hashes))
    (assets / 'envd.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')


def inspect_envd_wheel(path):
    path = Path(path)
    profile = load_profile()
    expected_pins = dependency_pins(profile=profile)
    assets = PACKAGE + '/_assets/'
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate execution archive entries')
        for item in archive.infolist():
            parts = PurePosixPath(item.filename)
            kind = stat.S_IFMT(item.external_attr >> 16)
            if (parts.is_absolute() or '..' in parts.parts or '\\' in item.filename or
                    item.is_dir() or kind not in (0, stat.S_IFREG)):
                raise ValueError('Unsafe execution archive entry')
        metadata_names = [name for name in names if name.endswith('.dist-info/METADATA')]
        if len(metadata_names) != 1:
            raise ValueError('Missing execution package metadata')
        prefix = metadata_names[0].removesuffix('METADATA')
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        if metadata['Name'] != DISTRIBUTION:
            raise ValueError('Only the execution distribution is accepted')
        manifest = json.loads(archive.read(assets + 'envd.json'))
        native = json.loads(archive.read(assets + 'native.json'))
        target = load_config()['targets'].get(native['target'])
        if target is None or target['os'] != 'linux' or native['wheel_tag'] != target['wheel_tag']:
            raise ValueError('Invalid execution wheel platform')
        version = metadata['Version']
        if version != profile['version']:
            raise ValueError('Execution package version differs from the execution profile version')
        tag = native['wheel_tag']
        dist = DISTRIBUTION.replace('-', '_')
        if (prefix != f'{dist}-{version}.dist-info/' or path.name != f'{dist}-{version}-py3-none-{tag}.whl' or
                metadata['Requires-Python'] != tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['requires-python']):
            raise ValueError('Execution filename, version or Python contract differs')
        wheel = BytesParser().parsebytes(archive.read(prefix + 'WHEEL'))
        if wheel['Root-Is-Purelib'] != 'false' or wheel['Tag'] != 'py3-none-' + tag:
            raise ValueError('Invalid execution wheel tag')
        actual = metadata.get_all('Requires-Dist', [])
        def requirements(items):
            found = {}
            for text in items:
                req = Requirement(text)
                key = canonicalize_name(req.name)
                if key in found or req.marker or req.extras or req.url:
                    raise ValueError('Invalid execution dependency metadata')
                found[key] = str(req.specifier)
            return found
        if requirements(actual) != requirements(expected_pins):
            raise ValueError('Execution dependencies differ from the locked profile')
        if (set(manifest) != {'schema_version', 'package_version', 'protocol_version', 'target', 'files', 'dependencies', 'execution_sha256'} or
                manifest['schema_version'] != 1 or manifest['package_version'] != version or
                manifest['protocol_version'] != protocol_version() or manifest['target'] != native['target'] or
                manifest['dependencies'] != expected_pins or set(manifest['files']) != set(profile['files'])):
            raise ValueError('Invalid execution source manifest')
        required = set(profile['files']) | {assets + 'native.json', assets + 'envd.json'}
        if set(native['tools']) != {'rg', 'fd'}:
            raise ValueError('Missing execution native tools')
        for name, values in load_config()['tools'].items():
            entry = native['tools'][name]
            if entry['filename'] != name:
                raise ValueError('Invalid execution native filename')
            binary = assets + 'bin/' + name
            required.add(binary)
            required.update(assets + 'licenses/' + name + '-' + license for license in values['licenses'])
            if hashlib.sha256(archive.read(binary)).hexdigest() != entry['sha256']:
                raise ValueError('Execution binary SHA-256 mismatch')
        required.update(prefix + name for name in ('METADATA', 'WHEEL', 'RECORD', 'licenses/LICENSE'))
        optional = {prefix + 'top_level.txt'}
        if required - set(names) or set(names) - required - optional:
            raise ValueError('Missing or unexpected execution package content')
        hashes = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in profile['files']}
        if hashes != manifest['files'] or execution_digest(hashes) != manifest['execution_sha256']:
            raise ValueError('Execution source SHA-256 mismatch')
        expected = {name: hashlib.sha256(execution_source(ROOT, name, profile['version'])).hexdigest()
            for name in profile['files']}
        if (execution_digest(expected) != profile['execution_sha256'] or
                hashlib.sha256(json.dumps(expected_pins,separators=(',',':')).encode()).hexdigest() != profile['dependencies_sha256']):
            raise ValueError('Execution source or dependencies changed without an execution version update')
        if hashes != expected:
            raise ValueError('Execution source differs from the current build source')
    return dict(filename=path.name, version=version, target=native['target'], files=len(names), bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(), execution_sha256=manifest['execution_sha256'], dependencies=expected_pins)
