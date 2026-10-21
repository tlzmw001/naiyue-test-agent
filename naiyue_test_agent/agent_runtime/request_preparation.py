"""Prepare fixed tools, prompt and compaction before model execution."""

from .context_compaction import _acompact_messages
from .run_control import UserStop, native_async_model


PREPARED_REQUEST_KEY = '_naiyue_prepared_request'


async def prepare_request(state, runtime, prepared, *, toolset=None, compact=False):
    context = runtime.context
    model = prepared['model']
    control = context.get('_run_control')
    update = {}
    if toolset is not None:
        update = toolset.prepare_tool_view(runtime, names=prepared['active_tool_names'])
        tools, prompt = toolset._inputs({**state, **update}, runtime)
        prepared.update(tools=tools, system_message=prompt)
        if compact:
            call = lambda: _acompact_messages(state['messages'], model, model_id=prepared['model_id'],
                context=context, system_messages=[prompt], tools=tools)
            compressed = (await control.await_model(call) if control is not None and native_async_model(model)
                          else await call())
            if compressed:
                update.update(compressed)
    if control is not None and control.requested:
        raise UserStop()
    context[PREPARED_REQUEST_KEY] = prepared
    return update or None
