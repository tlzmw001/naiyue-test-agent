"""A tag cannot publish until the complete tested platform matrix has succeeded."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('job', ['targets', 'frontend'])
def test_inline_python_runs_from_a_runner_script_outside_checkout(tmp_path, job):
    workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
    code = next(step['run'] for step in workflow['jobs'][job]['steps']
                if step.get('shell') == 'python')
    workspace = tmp_path / 'checkout'
    for name in ('scripts/native_assets.py', 'scripts/release_artifacts.py',
                 'release/native-tools.json', 'naiyue_test_agent/__init__.py'):
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    frontend = workspace / 'frontend'
    (frontend / 'dist/assets').mkdir(parents=True)
    (frontend / 'dist/index.html').write_text('<html></html>')
    (frontend / 'dist/assets/app.js').write_text('console.log("fixture")')
    (frontend / 'package-lock.json').write_text(json.dumps({'packages': {}}))
    runner = tmp_path / 'runner.py'
    runner.write_text(code)
    env = {key: value for key, value in os.environ.items() if not key.startswith('PYTHON')}
    env.update({'GITHUB_OUTPUT': str(tmp_path / 'outputs'), 'GITHUB_REF_TYPE': 'branch'})
    result = subprocess.run([sys.executable, str(runner)], cwd=workspace, env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    if job == 'targets':
        matrix = json.loads((tmp_path / 'outputs').read_text().removeprefix('matrix='))
        assert matrix['include'] == [
            {'target': 'macos-arm64', 'runner': 'macos-14'},
            {'target': 'linux-x86_64', 'runner': 'ubuntu-22.04'},
            {'target': 'linux-arm64', 'runner': 'ubuntu-24.04-arm'},
            {'target': 'windows-x86_64', 'runner': 'windows-2022'},
        ]
    else:
        assert (workspace / 'build/frontend-assets/web/index.html').read_text() == '<html></html>'


def test_publish_waits_for_shared_ci_and_uses_only_oidc():
    workflow = yaml.load((ROOT / '.github/workflows/publish.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['on']['push']['tags'] == ['v*']
    checks = workflow['jobs']['checks']
    assert checks['uses'] == './.github/workflows/ci.yml'
    publish = workflow['jobs']['publish']
    assert publish['needs'] == ['checks']
    assert publish['environment']['name'] == 'pypi'
    assert publish['permissions'] == {'contents': 'read', 'id-token': 'write'}
    assert len(publish['steps']) == 2
    assert publish['steps'][-1]['uses'] == 'pypa/gh-action-pypi-publish@release/v1'
    assert 'secrets.' not in json.dumps(publish)
    assert workflow['jobs']['published-install']['needs'] == ['checks', 'publish']


def test_ci_checks_source_tests_and_all_wheels_before_upload_gate():
    workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
    assert 'workflow_call' in workflow['on']
    test_steps = json.dumps(workflow['jobs']['test']['steps'])
    assert 'python -m pytest tests -q' in test_steps
    wheel_steps = json.dumps(workflow['jobs']['wheels']['steps'])
    assert 'scripts.build_release' in wheel_steps and 'scripts.verify_wheel_install' in wheel_steps
    assert 'npm --prefix frontend test -- --run' in json.dumps(workflow['jobs']['frontend']['steps'])
    assert 'frontend' in workflow['jobs']['wheels']['needs']
    assert '--frontend-assets' in wheel_steps
    assert set(workflow['jobs']['complete']['needs']) == {'test', 'wheels'}
    assert '--complete-release' in json.dumps(workflow['jobs']['complete']['steps'])


def test_complete_release_rejects_missing_and_duplicate_targets(monkeypatch):
    from scripts import release_artifacts
    targets = json.loads((ROOT / 'release/native-tools.json').read_text())['targets']
    paths = [Path(name) for name in targets]
    monkeypatch.setattr(release_artifacts, 'inspect_wheel', lambda p: {
        'target': p.name, 'version': '0.1.0', 'application_sha256': 'same-source'})
    release_artifacts.inspect_release(paths, '0.1.0')
    with pytest.raises(ValueError, match='distinct platform wheels'):
        release_artifacts.inspect_release(paths[:-1], '0.1.0')
    with pytest.raises(ValueError, match='distinct platform wheels'):
        release_artifacts.inspect_release([*paths[:-1], paths[0]], '0.1.0')
    with pytest.raises(ValueError, match='version'):
        release_artifacts.inspect_release(paths, '0.2.0')


def test_complete_release_rejects_mixed_application_snapshots(monkeypatch):
    from scripts import release_artifacts
    targets = json.loads((ROOT / 'release/native-tools.json').read_text())['targets']
    monkeypatch.setattr(release_artifacts, 'inspect_wheel', lambda p: {
        'target': p.name, 'version': '0.1.0', 'application_sha256': p.name})
    with pytest.raises(ValueError, match='source'):
        release_artifacts.inspect_release([Path(name) for name in targets], '0.1.0')


def test_release_targets_and_native_assets_exclude_intel_macos():
    from scripts.native_assets import load_config
    config = load_config()
    targets = {'macos-arm64', 'linux-x86_64', 'linux-arm64', 'windows-x86_64'}
    assert set(config['targets']) == targets
    for tool in config['tools'].values():
        assert set(tool['assets']) == targets


def test_complete_release_rejects_extra_old_intel_wheel(monkeypatch):
    from scripts import release_artifacts
    paths = [Path(name) for name in ('macos-arm64', 'linux-x86_64', 'linux-arm64', 'windows-x86_64')]
    monkeypatch.setattr(release_artifacts, 'inspect_wheel', lambda p: {
        'target': p.name, 'version': '0.1.0', 'application_sha256': 'same-source'})
    release_artifacts.inspect_release(paths, '0.1.0')
    with pytest.raises(ValueError, match='distinct platform wheels'):
        release_artifacts.inspect_release([*paths, Path('macos-x86_64')], '0.1.0')
