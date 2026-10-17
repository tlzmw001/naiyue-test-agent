"""Run native async application tests and close every locally owned application.

Only pytest invocation/resource ownership lives here. Tests await the real
application API; persistent contexts use the production factory directly.
"""

import asyncio
from contextlib import AsyncExitStack
from contextvars import ContextVar
from functools import wraps

from naiyue_test_agent.application.async_application import build_async_local_application


_resources = ContextVar('native_application_test_resources')


def async_test(test):
    @wraps(test)
    def run(*args, **kwargs):
        async def scenario():
            async with AsyncExitStack() as resources:
                token = _resources.set(resources)
                try:
                    return await test(*args, **kwargs)
                finally:
                    _resources.reset(token)
        return asyncio.run(scenario())
    return run


async def build_application(*args, **kwargs):
    app = await build_async_local_application(*args, **kwargs)
    _resources.get().push_async_callback(app.aclose)
    return app
