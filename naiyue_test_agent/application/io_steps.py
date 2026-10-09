"""Share finite application lifecycles while changing only their IO execution."""

from dataclasses import dataclass, field
from collections.abc import Callable

from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking


@dataclass
class IOCall:
    call: Callable
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    async_call: Callable | None = None
    critical: bool = True


def run_steps(steps):
    try:
        pending = next(steps)
        while True:
            try:
                result = pending.call(*pending.args, **pending.kwargs)
            except BaseException as error:
                pending = steps.throw(error)
            else:
                pending = steps.send(result)
    except StopIteration as completed:
        return completed.value
    finally:
        steps.close()


async def arun_steps(steps):
    try:
        pending = next(steps)
        while True:
            try:
                if pending.async_call is None:
                    result = await run_blocking(pending.call, *pending.args, **pending.kwargs)
                else:
                    operation = pending.async_call(*pending.args, **pending.kwargs)
                    result = await await_settled(operation) if pending.critical else await operation
            except BaseException as error:
                pending = steps.throw(error)
            else:
                pending = steps.send(result)
    except StopIteration as completed:
        return completed.value
    finally:
        steps.close()
