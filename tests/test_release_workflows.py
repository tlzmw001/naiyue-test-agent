"""A tag cannot publish until the complete tested platform matrix has succeeded."""

import json
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


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
    with pytest.raises(ValueError, match='five'):
        release_artifacts.inspect_release(paths[:-1], '0.1.0')
    with pytest.raises(ValueError, match='five'):
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
