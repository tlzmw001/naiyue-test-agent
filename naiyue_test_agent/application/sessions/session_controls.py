"""Session routing and the owning object's control-state operations."""

from naiyue_test_agent.agent_runtime.observation import observe, emit_event
from naiyue_test_agent.contracts.session_events import ControlData

import logging
from uuid import uuid4

from langchain_core.messages import HumanMessage, convert_to_messages

from naiyue_test_agent.application.errors import SessionBusyError
from .session_state import SessionRuntime
from naiyue_test_agent.contracts.replies import reply_identity, QueuedInput


logger = logging.getLogger('naiyue_test_agent.application')


def _tag_inputs(messages, *, kind='ordinary', reply_id=None):
    identity = reply_id or uuid4().hex
    return [message if kind == 'ordinary' and reply_identity(message) else message.model_copy(update={'id': message.id or uuid4().hex,
        'additional_kwargs': {**message.additional_kwargs, 'naiyue_reply_id': identity,
                              'naiyue_input_kind': kind}})
        if isinstance(message, HumanMessage) and not message.additional_kwargs.get('naiyue_compaction_summary')
        else message for message in messages]


def mark_human_messages(messages):
    result = []
    for message in convert_to_messages(messages):
        extra = {key: value for key, value in message.additional_kwargs.items() if key != 'naiyue_human_input'}
        if isinstance(message, HumanMessage) and not extra.get('naiyue_compaction_summary'):
            extra['naiyue_human_input'] = True
        result.append(message.model_copy(update={'additional_kwargs': extra}))
    return _tag_inputs(result)


class ControlQueue:
    """Borrow one runtime; mutate paired queues and stop targets under its lock."""

    def __init__(self, runtime: SessionRuntime, image_settings=None):
        from naiyue_test_agent.image_input_settings import ImageInputSettings
        self.image_settings = image_settings or ImageInputSettings()
        self._runtime, self.session_id = runtime, runtime.session_id

    def _current_control(self):
        # Caller holds the session state lock; this only borrows the reference.
        return self._runtime.run.observation.controls if self._runtime.run is not None and self._runtime.run.controls_open else None

    def steer(self, text, *, content=None, replace_ids=()):
        with self._runtime.lock:
            row = self._runtime.run
            if row is None or not row.steering_open:
                raise SessionBusyError('Session is not accepting steering messages')
            control, run_id = self._current_control(), row.run_id
            size = len(row.steering_queue)
            item = QueuedInput(uuid4().hex, text, 'steering', content)
            from .pending_images import retain
            retain(self._runtime, item, self.image_settings, replace_ids)
            row.steering_queue.append(item)
            if control:
                observe(control, control.accept, 'steering', queue_size=size)
        self._record_steering('steering_accepted', self.session_id, run_id, 1)
        if control:
            observe(control, control.flush)

    def follow_up(self, text, *, content=None, replace_ids=()):
        with self._runtime.lock:
            row = self._runtime.run
            if row is None or not row.steering_open:
                raise SessionBusyError('Session is not accepting follow-up messages')
            control, run_id = self._current_control(), row.run_id
            size = len(row.follow_up_queue)
            item = QueuedInput(uuid4().hex, text, 'follow_up', content)
            from .pending_images import retain
            retain(self._runtime, item, self.image_settings, replace_ids)
            row.follow_up_queue.append(item)
            if control:
                observe(control, control.accept, 'follow_up', queue_size=size)
        self._record_follow_up('follow_up_accepted', self.session_id, run_id, 1)
        if control:
            observe(control, control.flush)

    def request_scope_stop(self, *, run_id=None, agent_path='root', reason='user', agent_run_id=None, approval_batch_id=None):
        targets = []
        with self._runtime.lock:
            row = self._runtime.run
            active = row.observation if row is not None and row.controls_open else None
            if row is None:
                if reason != 'user':
                    return []
                raise SessionBusyError('Session has no active run to stop')
            if run_id is not None and run_id != row.run_id:
                raise SessionBusyError('Stop request targets a different run')
            retained, control, stop_control = [], None, None
            if agent_path == 'root':
                if row.stop_requested:
                    return []
                if reason == 'user' and ((not row.steering_open and (active is None or active.mode != 'compact'))
                                        or (active is not None and active.stage == 'finalize')):
                    raise SessionBusyError('Session is no longer accepting stop requests')
                retained = [item.text for item in [*row.steering_queue, *row.follow_up_queue]]
                row.stop_requested, row.steering_open = True, False
                if active is not None:
                    stop_control = active.stop_control
                    stop_control.mark_requested(reason)
                control = active.controls if active is not None and reason == 'user' else None
                if control:
                    observe(control, control.accept, 'stop')
            run_id = row.run_id
            if agent_path != 'root':
                instance = row.subagent_instances.get(agent_path)
                if (instance is None or instance['root_run_id'] != run_id
                        or instance['phase'] == 'finalize' or instance['settled']):
                    if reason != 'user':
                        return []
                    raise SessionBusyError('Subagent is no longer accepting stop requests')
                if agent_run_id is not None and instance['run_id'] != agent_run_id:
                    raise SessionBusyError('Stop request targets a different child execution')
                if approval_batch_id is not None and (instance.get('approval') is None
                        or instance['approval'].approval_batch_id != approval_batch_id):
                    raise SessionBusyError('Stop request targets a different child approval')
                candidates = [instance]
            else:
                candidates = [instance for instance in row.subagent_instances.values()
                              if instance['root_run_id'] == run_id]
            for instance in candidates:
                if instance['phase'] == 'finalize' or instance['status'] == 'stopping':
                    continue
                instance['status'] = 'stopping'
                instance['control'].mark_requested(reason)
                pending = instance.get('approval')
                if pending is not None and pending.status == 'pending':
                    pending.status = 'invalidated'
                targets.append((instance, pending))
        if stop_control is not None:
            stop_control.request(reason)
        for instance, pending in targets:
            instance['control'].request(reason)
            if reason == 'user':
                emit_event(instance.get('publisher_view'), 'control_accepted',
                    lambda: ControlData(uuid4().hex, 'stop'))
            if pending is not None and pending.status == 'invalidated':
                def wake(future=pending.future):
                    if not future.done():
                        future.set_result(([], None))
                pending.future.get_loop().call_soon_threadsafe(wake)
        logger.info('stop_requested', extra={'stop_event': {
            'event': 'stop_requested', 'session_id': self.session_id, 'run_id': run_id,
            'agent_path': agent_path, 'reason': reason,
        }})
        if control:
            observe(control, control.flush)
        return retained

    def is_stop_requested(self):
        with self._runtime.lock:
            return self._runtime.run.stop_requested if self._runtime.run else False

    def drain_steering(self):
        with self._runtime.lock:
            row = self._runtime.run
            if row is None:
                return []
            control, run_id = self._current_control(), row.run_id
            messages = list(row.steering_queue)
            row.steering_queue.clear()
            for item in messages:
                if item.message_id in self._runtime.pending_images:
                    self._runtime.pending_images[item.message_id]['status'] = 'selected'
            if control and messages:
                observe(control, control.select, 'steering', len(messages))
        if messages:
            self._record_steering('steering_injected', self.session_id, run_id, len(messages))
        if control:
            observe(control, control.flush)
        return messages

    def finish_run_boundary(self):
        with self._runtime.lock:
            row = self._runtime.run
            if row is None:
                return False, []
            if row.stop_requested:
                row.steering_open = False
                return True, []
            if row.steering_queue:
                messages, kind = list(row.steering_queue), 'steering'
                row.steering_queue.clear()
            elif row.follow_up_queue:
                messages, kind = [row.follow_up_queue.popleft()], 'follow_up'
            else:
                row.steering_open = False
                return False, []
            control, run_id = self._current_control(), row.run_id
            for item in messages:
                if item.message_id in self._runtime.pending_images:
                    self._runtime.pending_images[item.message_id]['status'] = 'selected'
            if control:
                observe(control, control.select, kind, len(messages))
        if kind == 'steering':
            self._record_steering('steering_injected', self.session_id, run_id, len(messages))
        else:
            self._record_follow_up('follow_up_injected', self.session_id, run_id, len(messages))
        if control:
            observe(control, control.flush)
        return False, messages

    def prepare_control_messages(self, messages):
        with self._runtime.lock:
            control = self._current_control()
            run = self._runtime.run
            kind = messages[0].kind
            prepared = [HumanMessage(id=item.message_id, content=item.content if item.content is not None else item.text) for item in messages]
            prepared = _tag_inputs(mark_human_messages(prepared), kind=kind,
                                  reply_id=run.reply_id if run and kind == 'steering' else None)
            for message in prepared:
                if isinstance(message.content,list) and run:
                    message.additional_kwargs['naiyue_image_origin_run_id'] = run.run_id
            if run and prepared:
                run.reply_id = prepared[-1].additional_kwargs['naiyue_reply_id']
        if control is not None:
            observe(control, control.record_messages, prepared)
        return prepared

    def pending_inputs(self):
        from .pending_images import views
        with self._runtime.lock:
            records = tuple((key, dict(row)) for key, row in self._runtime.pending_images.items())
        return views(records)

    def retain_input(self,message,replace_ids=()):
        from .pending_images import retain
        from naiyue_test_agent.contracts.image_content import input_text
        with self._runtime.lock:
            retain(self._runtime,QueuedInput(message.id,input_text(message.content),'ordinary',message.content),
                self.image_settings,replace_ids)

    def release_waiting_inputs(self,identities):
        with self._runtime.lock:
            for ident in identities:
                row=self._runtime.pending_images.get(ident)
                if row is not None and row['status']=='queued' and row['run_id'] is None:row['status']='retained'

    def pending_text(self, message_id):
        from naiyue_test_agent.contracts.image_content import input_text
        from naiyue_test_agent.contracts.image_inputs import ImageInputError
        with self._runtime.lock:
            row = self._runtime.pending_images.get(message_id)
            if row is None:
                raise ImageInputError('image_not_found',status=404)
            item = row['item']
        return input_text(item.content)

    def pending_image(self, message_id, index):
        from .pending_images import content
        with self._runtime.lock:
            return content(self._runtime,message_id,index)

    def validate_pending(self, identities):
        from .pending_images import validate_release
        with self._runtime.lock:
            validate_release(self._runtime, identities)

    def remove_pending(self, message_id):
        with self._runtime.lock:
            row=self._runtime.pending_images.get(message_id)
            if row and row['status'] in ('queued','selected'):
                raise SessionBusyError('Input is still active')
            self._runtime.pending_images.pop(message_id,None)

    def confirm_saved_inputs(self, messages):
        ids={m.id for m in messages}
        with self._runtime.lock:
            for key in list(self._runtime.pending_images):
                if key in ids:
                    del self._runtime.pending_images[key]

    @staticmethod
    def _record_steering(event: str, session_id: str, run_id: str, count: int) -> None:
        logger.info(event, extra={'steering_event': {
            'event': event, 'session_id': session_id, 'run_id': run_id, 'count': count,
        }})

    @staticmethod
    def _record_follow_up(event: str, session_id: str, run_id: str, count: int) -> None:
        logger.info(event, extra={'follow_up_event': {
            'event': event, 'session_id': session_id, 'run_id': run_id, 'count': count,
        }})

    def close_control_window(self, run):
        with self._runtime.lock:
            self._runtime.require_run(run)
            row, observation = self._runtime.state, run.observation
            row.pending_turn = observe(observation.turns, observation.turns.snapshot,
                pending=row.pending_tool_approval is not None)
            for record in self._runtime.pending_images.values():
                if record['run_id'] == run.run_id:
                    record['status'] = ('uncertain' if record['status'] == 'selected' else 'retained')
            discarded, follow_up = len(run.steering_queue), len(run.follow_up_queue)
            run.steering_queue.clear()
            run.follow_up_queue.clear()
            observe(observation.controls, observation.controls.close, observation.control_close_reason())
            run.controls_open = run.steering_open = False
            run.stop_requested = False
            run.prompt_parts, run.project_instructions = [], None
            run.background_notices = None
            return row.manual_review_required, discarded, follow_up
