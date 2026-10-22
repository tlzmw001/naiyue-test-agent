"""Prepare fixed tools, prompt and compaction before model execution."""

from .context_compaction import _acompact_messages
from .context_usage import PARTS_KEY
from .run_control import UserStop, native_async_model
from ..tools.background_tasks import BackgroundNoticeSnapshot


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
        notices = context.get('_background_notices')
        if (isinstance(notices, BackgroundNoticeSnapshot) and context.get('agent_path', 'root') == 'root'
                and (notices.session_id, notices.workspace_root, notices.run_id) ==
                (context.get('session_id'), context.get('workspace_root'), context.get('run_id'))):
            start = 0
            for part in prompt.response_metadata.get(PARTS_KEY, ()):
                end = start + part['chars']
                if part['id'] == 'background_tasks' and prompt.content[start:end] == notices.text and notices.versions:
                    prepared['background_notices'] = notices
                start = end
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
