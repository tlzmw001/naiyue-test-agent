"""Config loading uses normal filesystem access without private-mode requirements."""

import json
from pathlib import Path
import stat

import pytest

from naiyue_test_agent.model_access import ModelConfigError, load_model_catalog
from test_model_access import config_for_all_kinds, write_config


@pytest.mark.parametrize('mode', [0o600, 0o644, 0o664, 0o666, 0o444])
def test_readable_model_config_loads_without_mode_restriction(tmp_path, mode):
    path = write_config(tmp_path, config_for_all_kinds())
    path.chmod(mode)
    before_mode = stat.S_IMODE(path.stat().st_mode)
    before_bytes = path.read_bytes()
    catalog = load_model_catalog(path)
    assert catalog.default_model_id == 'gateway'
    assert catalog.get('gateway').model_name == 'gateway-test-model'
    assert path.read_bytes() == before_bytes
    assert stat.S_IMODE(path.stat().st_mode) == before_mode


@pytest.mark.parametrize('content,message', [
    ('{', 'invalid JSON'),
    ('{"schema_version":1,"schema_version":2}', 'duplicate'),
    (json.dumps({**config_for_all_kinds(), 'default_model_id': 'missing'}), 'default_model_id'),
])
def test_nonprivate_config_still_validates_json_and_structure(tmp_path, content, message):
    path = tmp_path / 'models.json'
    path.write_text(content, encoding='utf-8')
    path.chmod(0o644)
    with pytest.raises(ModelConfigError, match=message):
        load_model_catalog(path)


def test_real_read_failure_is_reported_without_sensitive_os_detail(tmp_path, monkeypatch):
    path = write_config(tmp_path, config_for_all_kinds())
    path.chmod(0o644)
    original = Path.read_text
    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError('PRIVATE_OS_DETAIL')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', denied)
    with pytest.raises(ModelConfigError, match=r'cannot read model configuration \(PermissionError\)') as caught:
        load_model_catalog(path)
    assert 'PRIVATE_OS_DETAIL' not in str(caught.value)


def test_nonregular_config_is_rejected_before_content_read(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('A directory must never enter JSON reading')
    monkeypatch.setattr(Path, 'read_text', forbidden)
    with pytest.raises(ModelConfigError, match='must be a regular file'):
        load_model_catalog(tmp_path)
