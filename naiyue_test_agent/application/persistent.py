"""Compose the same application with official durable checkpoints."""

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph._internal._constants import CONFIG_KEY_DURABILITY

from naiyue_test_agent import application
from .session_persistence import SessionPersistence


class PersistentApplication(application.LocalApplication):
    def get_session_history(self, session_id):
        return self._sessions._persistence.history(session_id, self._agent)

    def recover_session(self, session_id, *, checkpoint_id, action):
        return self._sessions._persistence.recover(session_id, self._agent, checkpoint_id=checkpoint_id, action=action)

    def restore_session(self, session_id):
        return self._sessions._persistence.restore(session_id, self._agent)

    def list_sessions(self):
        return self._sessions._persistence.storage.list_sessions()


def build_persistent_application(config_path, *, storage, tools, system_prompt, response_format, initial_active_tools=None, settings=None):
    settings = settings if settings is not None else application.load_tool_settings()
    registry = application.prepare_tool_registry(tools)
    application.validate_permission_rules(settings.permissions, registry.select(registry.names()))
    initial = registry.names() if initial_active_tools is None else initial_active_tools
    registry.select(initial)
    catalog = application.load_model_catalog(config_path)
    sessions = application.SessionService(catalog, registry, initial_active_tools=initial, permission_settings=settings.permissions, progress_settings=settings.progress)
    sessions._persistence = SessionPersistence(storage, sessions)
    saver = SqliteSaver(storage.checkpoint_connection)
    agent = application._assemble_application(catalog, sessions, saver,
        tool_registry=registry, system_prompt=system_prompt, response_format=response_format)
    agent = agent.with_config({'configurable': {CONFIG_KEY_DURABILITY: 'sync'}})
    return PersistentApplication(catalog.default_model_id, sessions, agent, registry)
