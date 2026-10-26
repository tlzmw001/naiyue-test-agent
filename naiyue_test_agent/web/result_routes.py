"""Authenticated scoped session result commands and read-only review."""
from fastapi import APIRouter,Depends,Query
from pydantic import BaseModel,ConfigDict,Field,field_validator
from naiyue_test_agent.contracts.git_inputs import GitInputError,input_path
from naiyue_test_agent.contracts.git_delivery import DeliveryCommand
from typing import Literal
from .dependencies import get_service


class CaptureResult(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    result_id:str=Field(pattern='^[0-9a-f]{32}$')
    include_untracked:list[str]=Field(max_length=10000)

    @field_validator('include_untracked')
    @classmethod
    def paths(cls,value):
        try:
            for path in value:input_path(path)
        except GitInputError:
            raise ValueError('Invalid result path') from None
        if len(set(value))!=len(value):raise ValueError('Duplicate result path')
        return value


router=APIRouter()


@router.get('/sessions/{session_id}/results/options')
async def options(session_id:str,service=Depends(get_service)):
    return await service.application.sessions.result_options(session_id)


@router.post('/sessions/{session_id}/results')
async def capture(session_id:str,body:CaptureResult,service=Depends(get_service)):
    result=await service.application.sessions.capture_result(session_id,**body.model_dump())
    # A command receipt must fit the service frame even for large file lists.
    return {'result_id':result['result_id'],'state':result['state']}


@router.get('/sessions/{session_id}/results')
async def results(session_id:str,service=Depends(get_service)):
    return {'items':await service.application.sessions.results(session_id)}


@router.get('/sessions/{session_id}/results/{result_id}')
async def result(session_id:str,result_id:str,service=Depends(get_service)):
    return await service.application.sessions.result(session_id,result_id)


@router.get('/sessions/{session_id}/results/{result_id}/diff')
async def diff(session_id:str,result_id:str,path:str=Query(min_length=1,max_length=4096),service=Depends(get_service)):
    return await service.application.sessions.result_diff(session_id,result_id,path)


class AcknowledgeDelivery(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    operation_id:str=Field(pattern='^[0-9a-f]{32}$')
    confirmed:Literal[True]


@router.get('/sessions/{session_id}/results/{result_id}/delivery')
async def delivery_options(session_id:str,result_id:str,service=Depends(get_service)):
    return await service.application.sessions.delivery_options(session_id,result_id)


@router.post('/sessions/{session_id}/results/{result_id}/delivery')
async def deliver(session_id:str,result_id:str,body:DeliveryCommand,service=Depends(get_service)):
    return await service.application.sessions.deliver(session_id,result_id,body.model_dump())


@router.get('/sessions/{session_id}/results/{result_id}/delivery/operations')
async def delivery_operations(session_id:str,result_id:str,service=Depends(get_service)):
    return {'items':await service.application.sessions.delivery_operations(session_id,result_id)}


@router.post('/sessions/{session_id}/results/{result_id}/delivery/acknowledge')
async def acknowledge_delivery(session_id:str,result_id:str,body:AcknowledgeDelivery,service=Depends(get_service)):
    return await service.application.sessions.acknowledge_delivery(session_id,result_id,body.operation_id)
