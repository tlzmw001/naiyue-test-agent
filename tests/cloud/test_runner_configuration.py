"""One role parser across local and B settings; bounded private bootstrap data."""
import pytest
from pydantic import ValidationError
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.settings import AppSettings
from cloud_helpers import settings
from lifecycle_helpers import config
from test_runner_control import prepared
from test_runner_kernel import bootstrap


def role(**changes):
    return dict(name='tester',description='Verify',system_prompt='Read project',tool_names=['read'])|changes


def test_role_and_environment_roundtrip_through_the_real_private_bootstrap(tmp_path):
    value=role()
    local=AppSettings(subagents=[value])
    with open_cloud_runtime(config(tmp_path,subagents=[value],tool_environment={'values':{'TASK_MARKER':'B'}})) as runtime:
        b=prepared(runtime)
        assert b.subagents==local.subagents
        saved=type(b).model_validate_json(b.model_dump_json())
        assert saved.subagents==b.subagents and saved.tool_environment.values=={'TASK_MARKER':'B'}


@pytest.mark.parametrize('roles',[[role(),role()],[role(tool_names=['subagent'])],[role(name='../bad')]])
def test_invalid_or_recursive_roles_are_rejected_in_both_cloud_boundaries(tmp_path,roles):
    with pytest.raises(ValidationError):settings(tmp_path,subagents=roles)
    with pytest.raises(ValidationError):type(bootstrap()).model_validate(bootstrap().model_dump()|{'subagents':roles})


def test_large_runtime_configuration_is_rejected_before_preparing_any_task(tmp_path):
    large=role(system_prompt='x'*70000)
    with pytest.raises(ValidationError,match='runner_configuration_too_large'):settings(tmp_path,subagents=[large])
    with pytest.raises(ValidationError,match='runner_configuration_too_large'):
        type(bootstrap()).model_validate(bootstrap().model_dump()|{'subagents':[large]})


def test_unicode_role_uses_the_same_utf8_budget_as_the_bootstrap_writer(tmp_path):
    value=role(system_prompt='中'*11000)
    assert settings(tmp_path,subagents=[value]).subagents
    b=bootstrap();parsed=type(b).model_validate(b.model_dump()|{'subagents':[value]})
    assert type(b).model_validate_json(parsed.model_dump_json()).subagents==parsed.subagents
