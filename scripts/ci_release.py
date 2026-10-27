"""Promote only complete release artifacts from this exact trusted CI commit."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode

from scripts.release_artifacts import check_tag, inspect_release, source_version


def github(endpoint):
    # gh reads GH_TOKEN from the environment; never put credentials in argv/logs.
    result = subprocess.run(['gh', 'api', endpoint], check=True, stdout=subprocess.PIPE, text=True, timeout=60)
    return json.loads(result.stdout)


def trusted_runs(runs, repository, sha):
    return [run for run in runs if run.get('head_sha') == sha
            and run.get('event') == 'push' and run.get('head_branch') == 'main'
            and run.get('status') == 'completed' and run.get('conclusion') == 'success'
            and run.get('path') == '.github/workflows/ci.yml'
            and run.get('head_repository', {}).get('full_name') == repository]


def resolve(repository, sha):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid GitHub repository')
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Invalid source commit SHA')
    query = urlencode({'head_sha': sha, 'branch': 'main', 'event': 'push',
                       'status': 'success', 'per_page': 100})
    runs = github(f'repos/{repository}/actions/workflows/ci.yml/runs?{query}')['workflow_runs']
    for run in trusted_runs(runs, repository, sha):
        run_id = str(run['id'])
        if not run_id.isdecimal():
            raise ValueError('Invalid CI run ID')
        artifacts = github(f'repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100')
        if any(a.get('name') == 'release-candidate' and a.get('expired') is False
               for a in artifacts['artifacts']):
            return run_id
    return ''


def create(directory, repository, sha, run_id, version):
    wheels = sorted((directory / 'wheels').glob('*.whl'))
    reports = inspect_release(wheels, version)
    files = [{'filename': row['filename'], 'target': row['target'],
              'sha256': hashlib.sha256((directory / 'wheels' / row['filename']).read_bytes()).hexdigest()}
             for row in reports]
    manifest = {'schema': 1, 'repository': repository, 'source_sha': sha,
                'run_id': str(run_id), 'version': version, 'files': files}
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


def verify(directory, repository, sha, run_id, version):
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    expected = {'schema': 1, 'repository': repository, 'source_sha': sha,
                'run_id': str(run_id), 'version': version}
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError('Release candidate identity mismatch')
    rows = manifest['files']
    names = [row['filename'] for row in rows]
    if (len(set(names)) != len(names) or any(not re.fullmatch(r'[a-zA-Z0-9_.-]+\.whl', n) for n in names)
            or set(names) != {p.name for p in (directory / 'wheels').iterdir()}):
        raise ValueError('Unexpected release candidate file set')
    for row in rows:
        wheel = directory / 'wheels' / row['filename']
        if wheel.is_symlink() or hashlib.sha256(wheel.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Release candidate SHA256 mismatch')
    reports = inspect_release([directory / 'wheels' / name for name in names], version)
    if {(r['filename'], r['target']) for r in reports} != {(r['filename'], r['target']) for r in rows}:
        raise ValueError('Release candidate target mismatch')
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['resolve', 'create', 'verify', 'install'])
    parser.add_argument('--directory', type=Path, default=Path('dist'))
    parser.add_argument('--run-id')
    parser.add_argument('--target')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = source_version(root)
    repository = os.environ['GITHUB_REPOSITORY']
    sha = os.environ['GITHUB_SHA']
    if os.environ.get('GITHUB_REF_TYPE') == 'tag':
        check_tag(os.environ['GITHUB_REF_NAME'], version)
    if args.action == 'resolve':
        run_id = resolve(repository, sha)
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write('run_id=' + run_id + '\n')
        print('Reuse successful main CI run ' + run_id if run_id else 'No reusable exact-commit CI; run complete checks')
    elif args.action == 'create':
        create(args.directory, repository, sha, os.environ['GITHUB_RUN_ID'], version)
    elif args.action == 'verify':
        verify(args.directory, repository, sha, args.run_id, version)
    else:
        reports = verify(args.directory, repository, sha, args.run_id, version)
        selected = [r for r in reports if r['target'] == args.target]
        if len(selected) != 1 or args.output is None:
            raise ValueError('Installation requires exactly one target and an output path')
        subprocess.run([sys.executable, '-m', 'scripts.verify_wheel_install', '--wheel',
                        str(args.directory / 'wheels' / selected[0]['filename']), '--from-pypi',
                        '--output', str(args.output)], check=True)


if __name__ == '__main__':
    main()
