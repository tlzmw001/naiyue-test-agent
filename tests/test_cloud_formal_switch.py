"""The formal product has one cloud chain and rejects retired A identities."""
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from naiyue_test_agent.settings import AppSettings, load_settings
from naiyue_test_agent.storage.execution_storage import root_from_row
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.web.schemas import ModelInput
from naiyue_test_agent.web.routes import create_api_router


@pytest.mark.parametrize('section', ['sandbox', 'delivery'])
def test_retired_configuration_is_rejected_without_rewriting_it(tmp_path, section):
    path = tmp_path / 'settings.toml'
    original = f'[{section}]\n'
    path.write_text(original)
    with pytest.raises(ValueError, match='unsupported_legacy_sandbox_configuration'):
        load_settings(path)
    assert path.read_text() == original
    with pytest.raises(ValidationError):
        AppSettings.model_validate({section: {}})


def test_retired_session_identity_cannot_become_a_directoryless_host():
    row = dict(project_id='P', execution_binding='sandbox', execution_root_kind=None,
        workspace_root=None, execution_setup_json='{"model_id":"M","include_untracked":[],"input_id":"' + 'a'*32 + '","binding_id":"' + 'b'*32 + '"}')
    original = row.copy()
    with pytest.raises(SessionPersistenceError, match='unsupported_execution_binding'):
        root_from_row(row)
    assert row == original


def test_new_sessions_reject_retired_execution_fields():
    assert ModelInput.model_validate({'model_id': 'M'}).model_dump() == {'model_id': 'M'}
    with pytest.raises(ValidationError):
        ModelInput.model_validate({'model_id': 'M', 'execution': 'sandbox'})


def test_formal_session_router_has_no_a_environment_or_result_endpoints():
    app = FastAPI()
    app.include_router(create_api_router())
    paths = list(app.openapi()['paths'])
    assert '/sessions/{session_id}' in paths
    assert not any('/environment/' in path or '/results' in path for path in paths)


def test_formal_project_router_does_not_create_url_only_a_projects():
    from naiyue_test_agent.service.projects import router
    app = FastAPI()
    app.include_router(router)
    paths = list(app.openapi()['paths'])
    assert any(path.endswith('/local') for path in paths)
    assert not any(path.endswith('/git') or path.endswith('/trust') for path in paths)


def test_local_handshake_rejects_the_pre_switch_service_protocol(tmp_path):
    from naiyue_test_agent.service.settings import ServiceError, ServiceOptions
    current = ServiceOptions(data_dir=tmp_path/'data', config_path=tmp_path/'models.json',
        settings_path=tmp_path/'settings.toml', frontend_dist=tmp_path/'assets').identity
    previous = current.model_copy(update={'protocol_version': 1})
    with pytest.raises(ServiceError, match='service_protocol_mismatch'):
        current.check(previous)
