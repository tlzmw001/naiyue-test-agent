"""Apply a manual summary to an idle official checkpoint, without running the graph."""

from uuid import uuid4

from langgraph.graph import END

from .async_utils import await_settled
from .context_compaction import _acompact_messages
from .errors import ContextCompactionError
from .invocation_context import make_invocation_context
from .message_observation import MessageStateObserver
from .observation import observe
from .run_control import UserStop, native_async_model


class ManualCompactionCommitError(RuntimeError):
    """A replacement write or its confirmation failed; further runs need recovery."""


async def compact_checkpoint(agent, *, session_id, thread_id, run_id, model_id, model, system_message, tools):
    config = {'configurable': {'thread_id': thread_id}}
    snapshot = await agent.aget_state(config)
    if snapshot.next or any(task.interrupts or task.error for task in snapshot.tasks):
        raise RuntimeError('Manual compaction requires a completed checkpoint')
    if not (snapshot.config or {}).get('configurable', {}).get('checkpoint_id'):
        # Initialize even when a stop was already accepted, so persistence can confirm ready.
        try:
            await await_settled(agent.aupdate_state(config, None, as_node=END))
            snapshot = await agent.aget_state(config)
        except Exception as error:
            raise ManualCompactionCommitError('Empty checkpoint creation is unconfirmed') from error
    messages = snapshot.values.get('messages', [])
    context = make_invocation_context(session_id, run_id, None, None, ())
    control = context.get('_run_control')
    publisher = context.get('_run_event_publisher')
    observer = MessageStateObserver(publisher) if publisher is not None else None
    if observer is not None:
        observe(observer, observer.seed, messages)
    operation = context.get('_context_observer')
    status = 'skipped'
    update = None
    try:
        call = lambda: _acompact_messages(messages, model, model_id=model_id, context=context,
            system_messages=[system_message], tools=tools, force=True, trigger='manual')
        update = await control.await_model(call) if control is not None and native_async_model(model) else await await_settled(call())
    except UserStop:
        return {**snapshot.values, 'run_stopped': True}
    except ContextCompactionError:
        # Policy already emitted its fixed reason; no checkpoint write has started.
        status = 'failed'
    if control is not None and control.requested:
        return {**snapshot.values, 'run_stopped': True}
    if update is not None:
        update['messages'][1].id = uuid4().hex
        expected = [message.id for message in update['messages'][1:]]
        async def commit():
            written = await agent.aupdate_state(snapshot.config, update, as_node='model')
            await agent.aupdate_state(written, None, as_node=END)
        try:
            await await_settled(commit())
            saved = await agent.aget_state(config)
            if (saved.next or any(task.error or task.interrupts for task in saved.tasks)
                    or [message.id for message in saved.values.get('messages', [])] != expected):
                raise RuntimeError('Manual compaction replacement is unconfirmed')
        except Exception as error:
            raise ManualCompactionCommitError('Manual compaction replacement is unconfirmed') from error
        snapshot = saved
        status = 'applied'
        if observer is not None:
            observe(observer, observer.observe, snapshot.values['messages'])
        if operation is not None:
            observe(operation, operation.observe, snapshot.values['messages'])
    return {**snapshot.values, 'compaction_status': status,
            'run_stopped': control.requested if control is not None else False}
