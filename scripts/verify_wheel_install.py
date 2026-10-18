"""Inspect before installation, then run the real installed product in a fresh venv."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import venv

from scripts.release_artifacts import inspect_wheel


def published_file(info, destination):
    url = f'https://pypi.org/pypi/naiyue-test-agent/{info["version"]}/json'
    for attempt in range(12):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                data = json.load(response)
            break
        except urllib.error.HTTPError as error:
            print(f'PyPI metadata status={error.code} attempt={attempt + 1}', flush=True)
            if error.code != 404 or attempt == 11:
                raise
            time.sleep(5)
    found = next(item for item in data['urls'] if item['filename'] == info['filename'])
    if found['digests']['sha256'] != info['sha256']:
        raise ValueError('Published artifact SHA-256 differs from tested artifact')
    with urllib.request.urlopen(found['url'], timeout=30) as response:
        content = response.read()
    if hashlib.sha256(content).hexdigest() != info['sha256']:
        raise ValueError('Downloaded artifact SHA-256 mismatch')
    path = destination / info['filename']
    path.write_bytes(content)
    inspect_wheel(path)
    return path


def verify_install_report(report, info, *, from_pypi=False):
    rows = [row for row in report['install'] if row['metadata']['name'].lower().replace('_', '-') == 'naiyue-test-agent']
    if len(rows) != 1 or rows[0]['metadata']['version'] != info['version']:
        raise ValueError('Unexpected installed project version')
    download = rows[0]['download_info']
    if download['archive_info']['hashes'].get('sha256') != info['sha256']:
        raise ValueError('Actual installed wheel SHA-256 differs from tested artifact')
    if from_pypi:
        parsed = urllib.parse.urlsplit(download['url'])
        if (parsed.scheme != 'https' or parsed.hostname != 'files.pythonhosted.org'
                or parsed.username or parsed.password
                or urllib.parse.unquote(parsed.path.rsplit('/', 1)[-1]) != info['filename']):
            raise ValueError('Actual installed wheel did not come from public PyPI')


def main():
    if sys.flags.optimize:
        raise RuntimeError('Installation smoke requires Python assertions enabled')
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--wheel', type=Path)
    source.add_argument('--wheel-dir', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--from-pypi', action='store_true')
    args = parser.parse_args()
    if args.wheel_dir:
        candidates = list(args.wheel_dir.glob('*.whl'))
        if len(candidates) != 1:
            raise ValueError('Installation verification requires exactly one wheel')
        wheel = candidates[0].resolve()
    else:
        wheel = args.wheel.resolve()
    info = inspect_wheel(wheel)
    worker = Path(__file__).resolve().with_name('release_smoke.py')
    with tempfile.TemporaryDirectory(prefix='nyt-install-') as directory:
        root = Path(directory)
        if args.from_pypi:
            wheel = published_file(info, root)
        environment = root / 'venv'
        venv.EnvBuilder(with_pip=True).create(environment)
        bin_path = environment / ('Scripts' if os.name == 'nt' else 'bin')
        python = bin_path / ('python.exe' if os.name == 'nt' else 'python')
        env = dict(os.environ)
        for key in list(env):
            if key.startswith(('NAIYUE_', 'OPENAI_', 'ANTHROPIC_', 'LANGSMITH_', 'LANGCHAIN_', 'PYTHON', 'PIP_')):
                del env[key]
        home = root / 'home'
        home.mkdir()
        env.update({'HOME': str(home), 'USERPROFILE': str(home), 'PATH': str(bin_path),
            'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false', 'PYTHONUTF8': '1',
            'PIP_CONFIG_FILE': os.devnull})
        outside = root / 'outside-checkout'
        outside.mkdir()
        install = ['naiyue-test-agent==' + info['version']] if args.from_pypi else [str(wheel)]
        install_report = root / 'pip-install.json'
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
            '--index-url', 'https://pypi.org/simple', '--report', str(install_report),
            '--only-binary=naiyue-test-agent', *install], cwd=outside, env=env, check=True, timeout=300)
        verify_install_report(json.loads(install_report.read_text(encoding='utf-8')), info, from_pypi=args.from_pypi)
        subprocess.run([str(python), '-m', 'pip', 'check'], cwd=outside, env=env, check=True, timeout=30)
        nyt = bin_path / ('nyt.exe' if os.name == 'nt' else 'nyt')
        for args_list in (['--help'], ['--version'], ['chat', '--help']):
            result = subprocess.run([str(nyt), *args_list], cwd=outside, env=env,
                capture_output=True, text=True, check=True, timeout=30)
            assert result.stdout.strip()
        result = subprocess.run([str(python), str(worker)], cwd=outside, env=env,
            capture_output=True, text=True, timeout=120)
        if result.returncode:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            raise RuntimeError('Installed product smoke failed')
        report = {**info, 'install': json.loads(result.stdout.strip().splitlines()[-1])}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
