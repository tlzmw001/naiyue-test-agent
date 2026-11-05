"""Published configuration examples use the real strict parsers."""
import json
from pathlib import Path
import tomllib
from naiyue_test_agent.settings import load_settings
from naiyue_test_agent.cloud_files.profiles import ProfileBook
from naiyue_test_agent.cloud_files.connections import ConnectionBook
from naiyue_test_agent.cloud_files.client import CloudConnection
from naiyue_test_agent.application.cloud.settings import CloudSettings
from naiyue_test_agent.cloud_install.template import TemplateBuildSettings
from naiyue_test_agent.cloud_install.settings import UpdateSettings, RemoteUpdateSettings

ROOT=Path(__file__).resolve().parents[1]/'docs/examples/full'

def test_full_settings_loads_without_retired_configuration():
    path=ROOT/'settings.full.toml'
    value=tomllib.loads(path.read_text())
    assert 'sandbox' not in value and 'delivery' not in value
    assert all('allow_sandbox' not in row for row in value.get('mcp',{}).get('servers',{}).values())
    assert load_settings(path).cloud_tasks.result_limits.max_results==64

def test_cloud_examples_use_real_configuration_contracts():
    for name,model in [('connections',ConnectionBook),('template-build',TemplateBuildSettings),('profiles',ProfileBook),('connection',CloudConnection),('cloud',CloudSettings),('update',UpdateSettings),('remote-update',RemoteUpdateSettings)]:
        value=json.loads((ROOT/(name+'.example.json')).read_text())
        parsed=model.model_validate_json(json.dumps(value))
        assert parsed is not None
        guide=(ROOT/'cloud-configuration-reference.md').read_text()
        assert name+'.example.json' in guide
        for key in value:
            assert '`'+key+'`' in guide
