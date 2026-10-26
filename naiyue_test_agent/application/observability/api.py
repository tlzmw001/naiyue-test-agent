"""ObservationAPI operations with explicit dependencies and shared lifetime ownership."""
from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking


class ObservationAPI:
    def __init__(self, owner, sessions, usage_repo, diagnostic_repo, usage, diagnostics, *, metadata=None):
        self._owner, self._sessions = owner, sessions
        self._usage_repo, self._diagnostic_repo = usage_repo, diagnostic_repo
        self._usage, self._diagnostics = usage, diagnostics
        self._metadata = metadata

    def subscribe(self, handler, *, session_id=None):
        self._owner.check_open()
        return self._sessions.subscribe_events(handler, session_id=session_id)

    def configure_errors(self, *, model_id=None, model=None, runtime=None, application=None):
        self._owner.check_open()
        return self._sessions.configure_error_classifiers(
            model_id=model_id, model=model, runtime=runtime, application=application)

    async def usage(self, session_id, *, model_limit=20, after_model=None):
        with self._owner.operation():
            if self._metadata is not None:
                await run_blocking(self._metadata.get, session_id)
            return await run_blocking(self._usage_repo.read_model_usage, session_id,
                model_limit=model_limit, after_model=after_model)

    async def runs(self, session_id, *, limit=50, before=None):
        with self._owner.operation():
            if self._metadata is not None:
                await run_blocking(self._metadata.get, session_id)
            return await run_blocking(self._diagnostic_repo.read_diagnostic_runs, session_id, limit=limit, before=before)

    async def events(self, session_id, run_id, *, limit=50, after=0):
        with self._owner.operation():
            if self._metadata is not None:
                await run_blocking(self._metadata.get, session_id)
            return await run_blocking(self._diagnostic_repo.read_diagnostic_events, session_id, run_id, limit=limit, after=after)
