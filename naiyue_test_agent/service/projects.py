"""Authenticated project management commands; directory browsing belongs to R1-07."""
from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field


class LocalInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    root: str = Field(min_length=1, max_length=32768)
    trusted: bool
    expected_directory: str | None = Field(default=None, pattern='^[0-9a-f]{64}$')
    name: str | None = Field(default=None, min_length=1, max_length=120)


class CreateInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    folder_name: str = Field(min_length=1, max_length=255)
    trusted: bool
    expected_directory: str | None = Field(default=None, pattern='^[0-9a-f]{64}$')
    parent: str | None = Field(default=None, min_length=1, max_length=32768)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    git_init: bool = False


class GitInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    url: str = Field(min_length=1, max_length=4096)
    ref: str = Field(min_length=1, max_length=1024)
    name: str = Field(min_length=1, max_length=120)


class RenameInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    name: str = Field(min_length=1, max_length=120)


class TrustInput(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    expected_revision: int = Field(ge=1)
    trusted: bool


class RelocateInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    expected_root: str = Field(min_length=1, max_length=32768)
    root: str = Field(min_length=1, max_length=32768)
    trusted: bool


router = APIRouter(prefix='/api/v1/projects')


class ReconcileInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    operation_id: str = Field(min_length=1, max_length=200)


@router.get('')
async def projects(request: Request):
    api = request.app.state.application.projects
    return {'items': jsonable_encoder(await api.list()), 'default_parent_dir': api.default_parent_dir}


@router.get('/operations')
async def operations(request: Request):
    return {'items': jsonable_encoder(await request.app.state.application.projects.operations())}


@router.post('/reconcile')
async def reconcile(request: Request, body: ReconcileInput | None = None):
    return {'items': jsonable_encoder(await request.app.state.application.projects.reconcile(body.operation_id if body else None))}


@router.post('/local', status_code=201)
async def add_local(body: LocalInput, request: Request):
    return jsonable_encoder(await request.app.state.application.projects.add_local(
        **body.model_dump(), operation_id=request.headers['x-request-id']))


@router.post('/create', status_code=201)
async def create_local(body: CreateInput, request: Request):
    return jsonable_encoder(await request.app.state.application.projects.create_local(
        **body.model_dump(), operation_id=request.headers['x-request-id']))


@router.post('/git', status_code=201)
async def add_git(body: GitInput, request: Request):
    return jsonable_encoder(await request.app.state.application.projects.add_git(
        **body.model_dump(), operation_id=request.headers['x-request-id']))


@router.get('/{project_id}')
async def project(project_id: str, request: Request):
    return jsonable_encoder(await request.app.state.application.projects.get(project_id))


@router.get('/{project_id}/execution-options')
async def execution_options(project_id:str,request:Request):
    return await request.app.state.application.environments.options(project_id)


@router.post('/{project_id}/trust')
async def trust_git(project_id:str,body:TrustInput,request:Request):
    from naiyue_test_agent.contracts.projects import ProjectError
    if body.trusted is not True:raise ProjectError('project_trust_required')
    return jsonable_encoder(await request.app.state.application.projects.trust_git(project_id,
        expected_revision=body.expected_revision,operation_id=request.headers['x-request-id']))


@router.patch('/{project_id}')
async def rename(project_id: str, body: RenameInput, request: Request):
    return jsonable_encoder(await request.app.state.application.projects.rename(
        project_id, body.name, operation_id=request.headers['x-request-id']))


@router.post('/{project_id}/relocate')
async def relocate(project_id: str, body: RelocateInput, request: Request):
    return jsonable_encoder(await request.app.state.application.projects.relocate(
        project_id, **body.model_dump(), operation_id=request.headers['x-request-id']))


@router.post('/{project_id}/close')
async def close(project_id: str, request: Request):
    await request.app.state.scopes.close_project(project_id)
    return {'project_id': project_id}


@router.delete('/{project_id}')
async def remove(project_id: str, request: Request):
    return await request.app.state.application.projects.remove(
        project_id, operation_id=request.headers['x-request-id'])
