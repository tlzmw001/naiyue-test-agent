import pytest
from naiyue_test_agent.settings import load_settings


def test_delivery_config_loads_from_existing_root_settings(tmp_path):
    path=tmp_path/'settings.toml';path.write_text('[delivery]\nmax_commits=12\nmax_operations_per_project=40\n')
    settings=load_settings(path)
    assert settings.delivery.max_commits==12 and settings.delivery.max_operations_per_project==40


@pytest.mark.parametrize('value',['0','true','"many"'])
def test_invalid_delivery_budgets_reject_startup(tmp_path,value):
    path=tmp_path/'settings.toml';path.write_text('[delivery]\nmax_commits='+value+'\n')
    with pytest.raises(ValueError):load_settings(path)
