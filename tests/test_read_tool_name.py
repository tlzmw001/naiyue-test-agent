from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.application import SessionService
from naiyue_test_agent.application.core import Application


def test_read_definition_exposes_one_model_visible_name():
    spec = create_session_read_tool()
    assert spec.tool.name == 'read'
    assert set(spec.input_schema.model_fields) == {'path', 'offset', 'limit'}
    assert issubclass(spec.tool.args_schema, spec.input_schema)


def test_tool_approval_methods_have_no_old_read_only_aliases():
    assert not hasattr(SessionService, 'pending_read_approvals')
    assert not hasattr(SessionService, 'resume_read_approval')
    assert not hasattr(Application, 'pending_read_approvals')
    assert not hasattr(Application, 'resume_read_approval')
