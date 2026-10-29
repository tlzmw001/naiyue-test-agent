"""Controller file invocation: await remote IO, drain bounded local worker IO."""
import inspect
from naiyue_test_agent.agent_runtime.async_utils import run_blocking


async def file_call(method,*args,**kwargs):
    if inspect.iscoroutinefunction(method):return await method(*args,**kwargs)
    return await run_blocking(method,*args,**kwargs)
