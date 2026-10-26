"""Environment catalogs resolve immutable policies without contacting Docker."""
import json

import pytest
from pydantic import ValidationError

from naiyue_test_agent.settings import AppSettings, load_settings
from naiyue_test_agent.sandbox_settings import SandboxSettings


def catalog(default='python-tests'):
    return dict(default_profile=default, profiles={
        'python-tests': dict(name='Python tests', image='fixture:python'),
        'frontend-tests': dict(name='Node tests', image='fixture:node'),
    }, network=dict(mode='none'), cpus=3.0)


def test_templates_load_and_round_trip_without_docker(tmp_path, monkeypatch):
    monkeypatch.setattr('naiyue_test_agent.execution.docker_cli.DockerCLI.daemon',
        lambda *_: pytest.fail('configuration contacted Docker'))
    path = tmp_path / 'settings.toml'
    path.write_text('[sandbox]\ndefault_profile="python-tests"\ncpus=3.0\n'
        '[sandbox.profiles.python-tests]\nname="Python tests"\nimage="fixture:python"\n'
        '[sandbox.profiles.frontend-tests]\nname="Node tests"\nimage="fixture:node"\n')
    configured = load_settings(path).sandbox
    assert configured.default_profile == 'python-tests'
    assert isinstance(configured.profiles, tuple)
    assert configured.select().settings.image == 'fixture:python'
    assert configured.select('frontend-tests').settings.cpus == 3.0
    loaded = AppSettings.model_validate({'sandbox': configured.model_dump(mode='json')}).sandbox
    assert loaded == configured
    with pytest.raises(ValidationError):
        configured.profiles[0].name = 'changed'


def test_legacy_image_normalizes_to_one_template_and_no_second_image_source():
    settings = AppSettings.model_validate({'sandbox': {'image': 'fixture:legacy'}}).sandbox
    assert settings.default_profile == 'default'
    assert settings.select().settings.image == 'fixture:legacy'
    assert 'image' not in settings.model_dump()
    assert AppSettings.model_validate({'sandbox': settings.model_dump()}).sandbox == settings


def test_flat_runtime_policy_can_still_be_injected_into_app_settings():
    settings = AppSettings(sandbox=SandboxSettings(image='fixture:injected'))
    assert settings.sandbox.select().settings.image == 'fixture:injected'


@pytest.mark.parametrize('change', [
    dict(default_profile='missing'), dict(default_profile=None),
    dict(image='fixture:mixed'),
    dict(profiles={'Invalid': dict(name='wrong', image='fixture:one')}),
    dict(profiles={'python-tests': dict(name=' ', image='fixture:one')}),
    dict(profiles={'python-tests': dict(name='bad\nname', image='fixture:one')}),
    dict(profiles={'python-tests': dict(name='ok', image='--bad')}),
    dict(profiles={'python-tests': dict(name='ok', image='fixture:one', command='extra')}),
])
def test_invalid_catalog_is_rejected(change):
    with pytest.raises(ValidationError):
        AppSettings.model_validate({'sandbox': {**catalog(), **change}})


def test_selection_is_frozen_and_detects_policy_tampering():
    from naiyue_test_agent.contracts.session_environment import SandboxProfileSelection
    configured = AppSettings.model_validate({'sandbox': catalog()}).sandbox
    selection = configured.select('frontend-tests')
    assert selection.profile_id == 'frontend-tests'
    assert selection.settings.network.mode == 'none'
    assert SandboxProfileSelection.model_validate_json(selection.model_dump_json()) == selection
    data = json.loads(selection.model_dump_json())
    assert not {'profiles', 'default_profile'} & data['settings'].keys()
    data['settings']['image'] = 'fixture:other'
    with pytest.raises(ValidationError):
        SandboxProfileSelection.model_validate(data)
    with pytest.raises(ValidationError):
        selection.settings.image = 'fixture:other'


def test_missing_template_does_not_fall_back_to_default():
    from naiyue_test_agent.contracts.sandbox import SandboxError
    configured = AppSettings.model_validate({'sandbox': catalog()}).sandbox
    with pytest.raises(SandboxError, match='env_profile_unavailable'):
        configured.select('not-registered')
    with pytest.raises(SandboxError, match='env_image_required'):
        AppSettings().sandbox.select()


def test_saved_environment_profile_metadata_cannot_disagree_with_policy():
    from naiyue_test_agent.contracts.sandbox import SandboxRecord
    from naiyue_test_agent.contracts.session_environment import SandboxProfileSelection
    selected = AppSettings.model_validate({'sandbox': catalog()}).sandbox.select()
    record = SandboxRecord('B', 'P', 'S', 'running', 'D', 'container', 'volume', 'image-id',
        selected.settings.model_dump(mode='json'), profile_id=selected.profile_id,
        profile_name=selected.name, profile_revision=selected.revision)
    assert SandboxProfileSelection.from_record(record) == selected
    with pytest.raises(ValidationError):
        SandboxProfileSelection.from_record(record.changed(profile_name='changed'))
    legacy = record.changed(profile_id=None, profile_name=None, profile_revision=None)
    assert SandboxProfileSelection.from_record(legacy).profile_id == 'legacy'
    assert SandboxProfileSelection.from_record(legacy).settings.image == 'fixture:python'
