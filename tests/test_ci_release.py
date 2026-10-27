"""Only complete wheels tested at this exact trusted commit may be promoted."""

import copy
import json

import pytest

from scripts import ci_release


SHA = 'a' * 40
REPO = 'example/agent'


def candidate():
    return {'id': 42, 'head_sha': SHA, 'event': 'push', 'head_branch': 'main',
            'status': 'completed', 'conclusion': 'success',
            'path': '.github/workflows/ci.yml',
            'head_repository': {'full_name': REPO}}


def test_only_exact_trusted_successful_main_runs_are_candidates():
    good = candidate()
    bad = []
    for field, value in [('head_sha', 'b' * 40), ('event', 'pull_request'),
                         ('head_branch', 'feature'), ('status', 'in_progress'),
                         ('conclusion', 'failure'), ('path', '.github/workflows/other.yml'),
                         ('head_repository', {'full_name': 'fork/agent'})]:
        row = copy.deepcopy(good)
        row[field] = value
        bad.append(row)
    assert ci_release.trusted_runs([*bad, good], REPO, SHA) == [good]
    assert ci_release.trusted_runs(bad, REPO, SHA) == []


def test_resolve_ignores_missing_or_expired_artifacts(monkeypatch):
    calls = []
    def api(endpoint):
        calls.append(endpoint)
        if '/workflows/' in endpoint:
            return {'workflow_runs': [candidate()]}
        return {'artifacts': [{'name': 'release-candidate', 'expired': True}]}
    monkeypatch.setattr(ci_release, 'github', api)
    assert ci_release.resolve(REPO, SHA) == ''
    assert len(calls) == 2
    monkeypatch.setattr(ci_release, 'github', lambda endpoint: (
        {'workflow_runs': [candidate()]} if '/workflows/' in endpoint else
        {'artifacts': [{'name': 'release-candidate', 'expired': False}]}))
    assert ci_release.resolve(REPO, SHA) == '42'


def test_resolve_propagates_api_failure_without_treating_it_as_a_cache_miss(monkeypatch):
    failure = OSError('GitHub unavailable')
    def unavailable(_endpoint):
        raise failure
    monkeypatch.setattr(ci_release, 'github', unavailable)
    with pytest.raises(OSError) as caught:
        ci_release.resolve(REPO, SHA)
    assert caught.value is failure


def test_create_propagates_incomplete_platform_validation(tmp_path, monkeypatch):
    (tmp_path / 'wheels').mkdir()
    def incomplete(_wheels, _version):
        raise ValueError('Missing platform wheels')
    monkeypatch.setattr(ci_release, 'inspect_release', incomplete)
    with pytest.raises(ValueError, match='Missing platform'):
        ci_release.create(tmp_path, REPO, SHA, '42', '0.5.0')
    assert not (tmp_path / 'manifest.json').exists()


def manifest_fixture(tmp_path, monkeypatch):
    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    (wheels / 'fixture.whl').write_bytes(b'wheel bytes')
    monkeypatch.setattr(ci_release, 'inspect_release', lambda paths, version: [
        {'filename': p.name, 'target': 'fixture'} for p in paths])
    ci_release.create(tmp_path, REPO, SHA, '42', '0.5.0')
    return tmp_path / 'manifest.json'


def test_manifest_roundtrip_checks_complete_release(tmp_path, monkeypatch):
    manifest_fixture(tmp_path, monkeypatch)
    reports = ci_release.verify(tmp_path, REPO, SHA, '42', '0.5.0')
    assert reports == [{'filename': 'fixture.whl', 'target': 'fixture'}]


@pytest.mark.parametrize('field,value', [('repository', 'fork/agent'), ('source_sha', 'b'*40),
                                        ('run_id', '43'), ('version', '0.4.0'), ('schema', 2)])
def test_manifest_rejects_changed_identity(tmp_path, monkeypatch, field, value):
    manifest = manifest_fixture(tmp_path, monkeypatch)
    data = json.loads(manifest.read_text())
    data[field] = value
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='identity'):
        ci_release.verify(tmp_path, REPO, SHA, '42', '0.5.0')


@pytest.mark.parametrize('change', ['corrupt', 'extra', 'missing', 'duplicate', 'path'])
def test_manifest_rejects_corruption_and_unexpected_files(tmp_path, monkeypatch, change):
    manifest = manifest_fixture(tmp_path, monkeypatch)
    wheel = tmp_path / 'wheels/fixture.whl'
    if change == 'corrupt':
        wheel.write_bytes(b'changed')
    elif change == 'extra':
        (tmp_path / 'wheels/extra.whl').write_bytes(b'extra')
    elif change == 'missing':
        wheel.unlink()
    else:
        data = json.loads(manifest.read_text())
        if change == 'duplicate':
            data['files'].append(data['files'][0])
        else:
            data['files'][0]['filename'] = '../fixture.whl'
        manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        ci_release.verify(tmp_path, REPO, SHA, '42', '0.5.0')
