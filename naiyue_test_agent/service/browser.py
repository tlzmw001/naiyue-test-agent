"""Pin browser commands before receipt replay; only built static files are public."""
from fastapi import Request
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from naiyue_test_agent.web.auth import authorize
from naiyue_test_agent.web.errors import WebError


class BrowserBoundary:
    def __init__(self, app, *, owner):
        self.app, self.owner = app, owner

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            request = Request(scope)
            settings = self.owner.state.web_settings
            try:
                if request.headers.get('host') != settings.authority:
                    raise WebError('invalid_host', 403)
                instance = request.headers.get('x-service-instance')
                if scope['path'].startswith('/api/') and instance is not None:
                    authorize(request, settings)
                    if instance != self.owner.state.owner.record.instance_id:
                        raise WebError('service_instance_mismatch', 409)
            except WebError as error:
                response = JSONResponse({'error': {'code': error.code}}, status_code=error.status)
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


class BrowserFiles(StaticFiles):
    async def get_response(self, path, scope):
        # An obsolete or unknown API must never become a successful HTML response.
        if path == 'api' or path.startswith('api/'):
            return JSONResponse({'error': {'code': 'endpoint_not_found'}}, status_code=404)
        return await super().get_response(path, scope)
