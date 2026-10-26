"""Default project parent is configuration, not an execution permission boundary."""
import pytest

from naiyue_test_agent.settings import AppSettings, load_settings


def test_project_default_is_optional_and_loads_from_existing_settings(tmp_path):
    assert AppSettings().projects.default_parent_dir is None
    path = tmp_path / 'settings.toml'
    parent = tmp_path / 'not-created'
    path.write_text('[projects]\ndefault_parent_dir = ' + repr(str(parent)) + '\n')
    assert load_settings(path).projects.default_parent_dir == parent
    assert not parent.exists()


@pytest.mark.parametrize('value', ['', 'relative/path', 42, False, 'bad\0path'])
def test_project_default_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        AppSettings.model_validate({'projects': {'default_parent_dir': value}})


def test_repository_configuration_example_loads_with_optional_project_default():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'docs/examples/settings.example.toml'
    assert load_settings(path).projects.default_parent_dir is None
