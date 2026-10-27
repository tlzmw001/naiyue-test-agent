"""Authenticated local filesystem metadata, independent from execution scopes."""
from fastapi import APIRouter, Query, Request

router = APIRouter(prefix='/api/v1/directories')


@router.get('')
async def directories(request: Request, path: str | None = Query(None, min_length=1, max_length=32768),
                      show_hidden: bool = False, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    return await request.app.state.application.projects.directories(
        path, show_hidden=show_hidden, offset=offset, limit=limit)
