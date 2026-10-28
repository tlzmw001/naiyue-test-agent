"""Standalone execution-only image probe; temporary writes and no network or credentials."""
import argparse
import asyncio
import io
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--aitest-version')
args = parser.parse_args()

report = {
    'python': platform.python_version(),
    'architecture': platform.machine(),
    'libc': platform.libc_ver(),
    'kernel': platform.release(),
    'uid': os.getuid(),
    'cases': {},
}


def threads():
    values = []
    thread = threading.Thread(target=lambda: values.append('thread-ok'))
    thread.start()
    thread.join(timeout=10)
    assert values == ['thread-ok'] and not thread.is_alive()


def async_threads():
    async def execute():
        assert await asyncio.to_thread(lambda: 'async-ok') == 'async-ok'
    asyncio.run(execute())


def child_process():
    result = subprocess.run(
        [sys.executable, '-I', '-c', 'print("child-ok")'],
        capture_output=True, text=True, check=True, timeout=20,
    )
    assert result.stdout.strip() == 'child-ok'


def async_child_process():
    async def execute():
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-I', '-c', 'print("async-child-ok")',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        output, errors = await asyncio.wait_for(process.communicate(), timeout=20)
        assert process.returncode == 0 and output.strip() == b'async-child-ok', errors
    asyncio.run(execute())


def execution_runtime():
    import hashlib
    from importlib import metadata, util
    import naiyue_test_agent
    from PIL import Image
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    package = Path(naiyue_test_agent.__file__).parent
    assets = package / '_assets'
    manifest = json.loads((assets / 'envd.json').read_text())
    assert metadata.version('naiyue-test-agent-envd') == manifest['package_version'] == naiyue_test_agent.__version__
    from naiyue_test_agent.contracts.sandbox import PROTOCOL_VERSION
    assert manifest['protocol_version'] == PROTOCOL_VERSION
    actual_files = {file.relative_to(package.parent).as_posix(): file for file in package.rglob('*.py')}
    assert set(actual_files) == set(manifest['files'])
    digest = hashlib.sha256()
    for name, file in sorted(actual_files.items()):
        value = hashlib.sha256(file.read_bytes()).hexdigest()
        assert value == manifest['files'][name], name
        digest.update(name.encode() + b'\0' + bytes.fromhex(value))
    assert digest.hexdigest() == manifest['execution_sha256']
    assert Image.new('RGB', (1, 1)).size == (1, 1)
    for module in ('application', 'agent_runtime', 'model_access', 'cli', 'web', 'mcp_client'):
        assert util.find_spec('naiyue_test_agent.' + module) is None, module
    for module in ('openai', 'anthropic', 'ollama', 'langchain_openai', 'langchain_anthropic',
                   'langchain_ollama', 'fastapi', 'uvicorn', 'mcp', 'sqlite_vec'):
        assert util.find_spec(module) is None, module
    assert not (assets / 'web').exists()
    expected = {}
    for pin in manifest['dependencies']:
        requirement = Requirement(pin)
        name = canonicalize_name(requirement.name)
        assert requirement.specifier.contains(metadata.version(name)), pin
        expected[name] = str(requirement.specifier)
    if args.aitest_version:
        assert metadata.version('aitest-kit') == args.aitest_version
        executable = shutil.which('aitest')
        assert executable is not None
        help_result = subprocess.run([executable, '--help'], capture_output=True, text=True,
            check=True, timeout=20)
        assert 'doctor' in help_result.stdout and 'run' in help_result.stdout
        pending = ['aitest-kit']
        while pending:
            parent = pending.pop()
            if parent in expected:
                continue
            expected[parent] = metadata.version(parent)
            for text in metadata.requires(parent) or []:
                dependency = Requirement(text)
                if dependency.marker is not None and not dependency.marker.evaluate({'extra': ''}):
                    continue
                name = canonicalize_name(dependency.name)
                assert dependency.specifier.contains(metadata.version(name)), text
                pending.append(name)
        report['aitest_kit'] = {'version': args.aitest_version, 'cli': executable}
    installed = {canonicalize_name(item.metadata['Name']) for item in metadata.distributions()}
    assert not installed - set(expected) - {'naiyue-test-agent-envd', 'pip', 'setuptools', 'wheel'}, installed - set(expected)
    import naiyue_test_agent.envd.operations
    for module in ('builtin', 'subagent', 'history', 'memory', 'web_search', 'web_fetch'):
        assert 'naiyue_test_agent.tools.' + module not in sys.modules, module
    native = json.loads((assets / 'native.json').read_text())
    assert native['target'] == manifest['target']
    assert native['target'] == {'x86_64': 'linux-x86_64', 'aarch64': 'linux-arm64'}[platform.machine()]
    versions = {}
    for name in ('rg', 'fd'):
        assert native['tools'][name]['filename'] == name
        binary = assets / 'bin' / name
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == native['tools'][name]['sha256']
        result = subprocess.run([str(binary), '--version'], check=True, capture_output=True, text=True, timeout=20)
        versions[name] = result.stdout.splitlines()[0]
    report.update(package_version=naiyue_test_agent.__version__, distribution='naiyue-test-agent-envd',
        execution_sha256=digest.hexdigest(), source_files=len(actual_files), dependencies=len(expected),
        controller_and_model_sdks_absent=True, native_tools=versions)



def files():
    with tempfile.TemporaryDirectory(prefix='nyt-compat-') as directory:
        root = Path(directory)
        file = root / 'source.txt'
        file.write_text('utf8-中文\n')
        os.chmod(file, 0o600)
        assert file.read_text() == 'utf8-中文\n'
        target = root / 'renamed.txt'
        file.rename(target)
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as archive:
            archive.add(target, arcname='renamed.txt')
        data.seek(0)
        with tarfile.open(fileobj=data, mode='r') as archive:
            assert archive.extractfile('renamed.txt').read() == 'utf8-中文\n'.encode()
        with sqlite3.connect(root / 'test.sqlite') as database:
            assert database.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
            database.execute('CREATE TABLE probe (value TEXT NOT NULL)')
            database.execute('INSERT INTO probe VALUES (?)', ('saved',))
            assert database.execute('SELECT value FROM probe').fetchone()[0] == 'saved'


def unix_socket():
    with tempfile.TemporaryDirectory(prefix='nyt-socket-') as directory:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.bind(str(Path(directory) / 'probe.sock'))
            channel.listen(1)


for name, operation in (
    ('threads', threads), ('async_threads', async_threads),
    ('child_process', child_process), ('async_child_process', async_child_process),
    ('files_sqlite_tar', files), ('unix_socket', unix_socket),
    ('execution_runtime', execution_runtime),
):
    try:
        operation()
        report['cases'][name] = {'status': 'passed'}
    except Exception as error:
        report['cases'][name] = {'status': 'failed', 'type': type(error).__name__, 'message': str(error)}

report['passed'] = all(value['status'] == 'passed' for value in report['cases'].values())
print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
sys.exit(0 if report['passed'] else 1)
