"""Authenticated terminal preparation; clients never open the project database."""
from pathlib import Path
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from naiyue_test_agent.application.projects.selection import ProjectSelection
from naiyue_test_agent.web.dependencies import get_service


class PreparationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    root: Path | None = None
    project_id: str | None = None
    database_path: Path | None = None
    new_workspace: bool = False
    resume: bool = False
    trusted: bool = False


router = APIRouter(prefix='/api/v1/terminal')


@router.post('/prepare')
async def prepare(body: PreparationInput, request: Request):
    selection = ProjectSelection(request.app.state.application.projects)
    return await selection.prepare(**body.model_dump(), operation_id=request.headers['x-request-id'])
