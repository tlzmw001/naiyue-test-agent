"""Compose the same application with official durable checkpoints."""

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph._internal._constants import CONFIG_KEY_DURABILITY

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.tool_approval import approval_policies_for_tools
from .session_persistence import SessionPersistence


class PersistentApplication(application.LocalApplication):
    def restore_session(self, session_id):
        return self._sessions._persistence.restore(session_id, self._agent)

    def list_sessions(self):
        return self._sessions._persistence.storage.list_sessions()


def build_persistent_application(config_path, *, storage, tools, system_prompt, response_format):
    catalog = application.load_model_catalog(config_path)
    sessions = application.SessionService(catalog, approval_policies_for_tools(tools))
    sessions._persistence = SessionPersistence(storage, sessions)
    saver = SqliteSaver(storage.checkpoint_connection)
    agent = application._assemble_application(catalog, sessions, saver,
        tools=tools, system_prompt=system_prompt, response_format=response_format)
    agent = agent.with_config({'configurable': {CONFIG_KEY_DURABILITY: 'sync'}})
    return PersistentApplication(catalog.default_model_id, sessions, agent)
