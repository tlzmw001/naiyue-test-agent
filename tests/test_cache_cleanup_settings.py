import pytest

from naiyue_test_agent.settings import load_settings


def test_cache_cleanup_settings_load_and_reject_invalid_retention(tmp_path):
    config = tmp_path / 'settings.toml'
    config.write_text('[cache_cleanup]\ntool_output_retention_days = 9\n', encoding='utf-8')
    settings = load_settings(config).cache_cleanup
    assert settings.tool_output_retention_days == 9
    assert settings.cloud_input_retention_days == 3
    config.write_text('[cache_cleanup]\ncloud_input_retention_days = 0\n', encoding='utf-8')
    with pytest.raises(ValueError, match='Cache cleanup settings'):
        load_settings(config)
