from naiyue_test_agent.agent_runtime.tools import create_read_tool, create_session_read_tool
from naiyue_test_agent.application import LocalApplication, SessionService


def test_read_tool_factories_expose_one_model_visible_name(tmp_path):
    assert create_read_tool(tmp_path).name == 'read'
    assert create_session_read_tool().name == 'read'


def test_tool_approval_methods_have_no_old_read_only_aliases():
    assert not hasattr(SessionService, 'pending_read_approvals')
    assert not hasattr(SessionService, 'resume_read_approval')
    assert not hasattr(LocalApplication, 'pending_read_approvals')
    assert not hasattr(LocalApplication, 'resume_read_approval')
