"""Only the service's fixed settings file can refresh the environment catalog."""
from typing import Literal
from fastapi import APIRouter, Request, Path
from pydantic import BaseModel, ConfigDict


class RefreshInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


router = APIRouter(prefix='/api/v1/environments')


class ManagementInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    confirm_destroy: bool = False
    interrupt: bool = False


@router.get('')
async def management(request: Request):
    return await request.app.state.application.environments.management()


@router.post('/{binding_id}/{action}')
async def manage(request: Request, action: Literal['start','stop','recover','destroy'],
                 binding_id: str = Path(pattern=r'^[0-9a-f]{32}$'), body: ManagementInput | None = None):
    return await request.app.state.application.environments.manage(binding_id,action,
        confirm_destroy=body.confirm_destroy if body else False,interrupt=body.interrupt if body else False)


@router.post('/refresh')
async def refresh(request: Request, body: RefreshInput | None = None):
    return await request.app.state.application.environments.refresh(request.app.state.scopes.settings_path)
