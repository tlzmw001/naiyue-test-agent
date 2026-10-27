"""Stop producers before Uvicorn waits for their live SSE connections to finish."""
import logging

import uvicorn

logger = logging.getLogger(__name__)


class ServiceServer(uvicorn.Server):
    async def shutdown(self, sockets=None):
        primary = None
        app = self.config.app
        scopes = getattr(app.state, 'scopes', None)
        if scopes is not None:
            try:
                await scopes.shutdown(app.state.receipts)
            except BaseException as error:
                primary = error
        try:
            await super().shutdown(sockets=sockets)
        except BaseException as error:
            if primary is None:
                raise
            logger.error('service_server_cleanup_secondary_failed error_type=%s', type(error).__name__)
        if primary is not None:
            raise primary
