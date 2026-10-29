"""Keep operation tests on the production async owner with an in-process envd."""
import asyncio
from types import SimpleNamespace

import pytest

from naiyue_test_agent.application.environments.operations import SandboxOperationService
from naiyue_test_agent.execution.sandbox_runtime import SandboxRuntime


@pytest.fixture
def sandbox_operation_factory():
    runtimes=[]
    def create(service,*,activity):
        if not hasattr(service,'transport'):
            runtime=SandboxRuntime();runtimes.append(runtime)
            async def request(row,operation,params,**kwargs):
                return await asyncio.to_thread(service._engine(row).request,row,operation,params,**kwargs)
            service.transport=SimpleNamespace(runtime=runtime,arequest=request)
        if not hasattr(service,'check') and hasattr(service,'validate'):
            service.check=lambda binding,**_:service.validate(binding)
        return SandboxOperationService(service,activity=activity)
    yield create
    for runtime in runtimes:runtime.close()
