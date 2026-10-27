"""Prepare fixed tools, prompt and compaction before model execution."""

from dataclasses import replace

from .context_compaction import _acompact_messages
from .context_usage import PARTS_KEY
from .run_control import UserStop, native_async_model
from ..tools.background_tasks import BackgroundNoticeSnapshot


async def prepare_request(state, runtime, prepared, *, toolset=None, compact=False):
    context = runtime.context
    model = prepared.model
    control = context.scope.control
    update = {}
    if toolset is not None:
        update = toolset.prepare_tool_view(runtime, names=prepared.active_tool_names)
        tools, prompt = toolset._inputs({**state, **update}, runtime)
        prepared = replace(prepared, tools=tools, system_message=prompt)
        notices = context.scope.background_notices
        if (isinstance(notices, BackgroundNoticeSnapshot) and context.scope.identity.agent_path == 'root'
                and (notices.session_id, notices.workspace_root, notices.run_id) ==
                (context.scope.identity.session_id, context.scope.identity.execution_path, context.scope.identity.run_id)):
            start = 0
            for part in prompt.response_metadata.get(PARTS_KEY, ()):
                end = start + part['chars']
                if part['id'] == 'background_tasks' and prompt.content[start:end] == notices.text and notices.versions:
                    prepared = replace(prepared, background_notices=notices)
                start = end
        if compact:
            call = lambda: _acompact_messages(state['messages'], model, model_id=prepared.model_id,
                context=context, system_messages=[prompt], tools=tools)
            compressed = (await control.await_model(call) if control is not None and native_async_model(model)
                          else await call())
            if compressed:
                update.update(compressed)
    if control is not None and control.requested:
        raise UserStop()
    context.invocation.model_call.prepared = prepared
    return update or None
