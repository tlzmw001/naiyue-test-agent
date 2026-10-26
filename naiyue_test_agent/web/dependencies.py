"""Resolve one request's bound host without mutating global application state."""
from fastapi import Request


async def get_service(request: Request):
    scopes = getattr(request.app.state, 'scopes', None)
    if scopes is not None:
        return await scopes.get(request.path_params['scope_id'])
    return request.app.state.service
