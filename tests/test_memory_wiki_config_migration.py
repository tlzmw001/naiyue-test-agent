"""Removed memory policies fail explicitly before any store is opened."""
import pytest
from naiyue_test_agent.settings import load_settings
from naiyue_test_agent.memory_settings import MemorySettings
from pydantic import ValidationError


def test_old_automatic_recall_config_requires_migration(tmp_path):
    path=tmp_path/'settings.toml';path.write_text('[memory]\nrelated_limit=8\n')
    with pytest.raises(ValueError,match='memory_config_migration_required'):
        load_settings(path)


def test_kind_ttl_config_is_no_longer_accepted():
    with pytest.raises(ValidationError):MemorySettings(active_ttl_days={'convention':90})
