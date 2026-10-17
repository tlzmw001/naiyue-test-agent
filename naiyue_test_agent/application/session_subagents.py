"""Application-owned role definitions shared by this application's sessions."""

import logging
import asyncio
from contextvars import Context
from uuid import uuid4

from langchain_core.tools import ToolException

from naiyue_test_agent.contracts.failures import safe_error_type
from naiyue_test_agent.tools.subagent import SubAgentSpec
from naiyue_test_agent.tools.permissions import PermissionMechanismError, permission_binding
from naiyue_test_agent.agent_runtime.run_control import RunControl
from naiyue_test_agent.agent_runtime.events import AgentEventSource
from naiyue_test_agent.agent_runtime.observation import observe
from naiyue_test_agent.agent_runtime.run_services import current_run_services
from naiyue_test_agent.agent_runtime.tool_execution.permissions import collect_human_messages
from naiyue_test_agent.agent_runtime.subagents import ainvoke_subagent_step, _finish_subagent_result
from naiyue_test_agent.agent_runtime.async_utils import await_settled
from .run_events import RunObservation
from .subagent_history import subagent_identity


logger = logging.getLogger('naiyue_test_agent.application')


class SessionSubagentControls:
    def configure_subagent_runtime(self, checkpointer, result_settings):
        self._subagent_checkpointer = checkpointer
        self._subagent_result_settings = result_settings

    def register_subagent(self, spec):
        try:
            if not isinstance(spec, SubAgentSpec):
                raise ValueError('Subagent registration requires SubAgentSpec')
            self._tool_registry.select(spec.tool_names)
            if spec.model_id is not None:
                self._catalog.get(spec.model_id)
            with self._lock:
                if spec.name in self._subagents:
                    raise ValueError('Duplicate subagent name in registration')
            from naiyue_test_agent.agent_runtime.subagents import build_subagent_runtime
            agent = build_subagent_runtime(spec, catalog=self._catalog, tool_registry=self._tool_registry,
                checkpointer=self._subagent_checkpointer, resolve_model=self.resolve_model,
                resolve_active_tool_names=self.resolve_active_tool_names,
                resolve_permission_context=self.resolve_permission_context,
                resolve_prompt_parts=self.resolve_prompt_parts,
                permission_settings=self._permission_settings, permission_reviewer=self._permission_reviewer)
            with self._lock:
                if spec.name in self._subagents:
                    raise ValueError('Duplicate subagent name in registration')
                self._subagents[spec.name] = {'spec': spec, 'agent': agent}
        except Exception as error:
            logger.error('subagent_registration_failed error_type=%s', safe_error_type(error))
            raise

    def get_registered_subagents(self):
        with self._lock:
            return tuple(item['spec'] for item in self._subagents.values())

    def prepare_subagent_instance(self, agent, task, *, parent_context, parent_state, parent_tool_call_id):
        if parent_context.get('agent_path', 'root') != 'root':
            raise ToolException('Only one level of subagent delegation is supported.')
        session_id = parent_context['session_id']
        batch = parent_state.get('tool_execution', {})
        call = next((item for item in batch.get('calls', []) if item['id'] == parent_tool_call_id), None)
        permit = parent_context.get('_execution_permit')
        if (call is None or call['name'] != 'subagent' or call['args'] != {'agent': agent, 'task': task}
                or permit != permission_binding(parent_context, batch.get('batch_id'), call)):
            raise PermissionMechanismError('Subagent parent call does not match its execution permit')
        identity = subagent_identity(session_id, batch['batch_id'], parent_tool_call_id)
        with self._lock:
            session = self._sessions[session_id]
            if not session['busy'] or session['run_id'] != parent_context.get('run_id'):
                raise PermissionMechanismError('Subagent requires the current active parent run')
            if session['stop_requested']:
                raise ToolException('Subagent was not started because its owning run is stopping.')
            if agent not in self._subagents:
                raise ToolException('Unknown subagent role. Choose a registered role.')
            role = self._subagents[agent]
            spec = role['spec']
            path = f'root/{agent}/{identity}'
            key = (session_id, path)
            if key in self._subagent_instances:
                raise PermissionMechanismError('Subagent call has already been started; replay is not allowed')
            instance = dict(session_id=session_id, role=agent, agent_path=path,
                child_thread_id=f'subagent:{identity}', seed_id=f'subagent-seed:{identity}',
                parent_tool_call_id=parent_tool_call_id, parent_run_id=session['run_id'],
                root_run_id=parent_context.get('root_run_id', session['run_id']), run_id=uuid4().hex,
                model_id=spec.model_id or session['model_id'], tool_names=spec.tool_names,
                status='running', phase='execute', settled=False, task=task, workspace_root=session['workspace_root'],
                read_resource_roots=parent_context.get('read_resource_roots', ()),
                max_concurrency=parent_context.get('_max_concurrency', 4), control=RunControl(),
                publisher=parent_context.get('_run_event_publisher'), pending_turn=None,
                errors=parent_context.get('_failure_policy'), human_messages=collect_human_messages(parent_state['messages']))
            self._subagent_instances[key] = instance
            return role['agent'], instance

    def subagent_instance(self, session_id, agent_path):
        return self._subagent_instances[(session_id, agent_path)]

    async def ainvoke_subagent(self, agent, task, *, parent_context, parent_state, parent_tool_call_id):
        graph, instance = self.prepare_subagent_instance(agent, task, parent_context=parent_context,
            parent_state=parent_state, parent_tool_call_id=parent_tool_call_id)
        driver = asyncio.create_task(self._subagent_steps(graph, instance), context=Context())
        instance['driver'] = driver
        try:
            return await asyncio.shield(driver)
        except asyncio.CancelledError:
            self._request_scope_stop(instance['session_id'], run_id=instance['root_run_id'],
                                     agent_path=instance['agent_path'], reason='failure')
            try:
                await await_settled(driver)
            except BaseException as error:
                logger.info('subagent_cancel_settled error_type=%s', safe_error_type(error))
            raise
        except BaseException:
            if instance['status'] != 'stopped':
                instance['status'] = 'failed'
            raise
        finally:
            self.release_live_approval(instance)
            instance.pop('driver', None)
            instance['settled'] = driver.done()

    async def _subagent_steps(self, graph, instance):
        resume, grants = None, None
        while True:
            source = AgentEventSource(instance['agent_path'], instance['agent_path'].rsplit('/', 1)[-1],
                instance['run_id'], instance['parent_run_id'], instance['parent_tool_call_id'])
            instance['publisher_view'] = instance['publisher'].for_agent(source)
            observation = RunObservation(instance['publisher_view'],
                'approval_resume' if resume else 'message', progress_settings=self._progress_settings,
                stop_control=instance['control'])
            observation.runtime_errors = instance['errors']
            observe(observation.turns, observation.turns.restore, instance['pending_turn'])
            pending = None
            try:
                observation.start()
                observation.stage = 'execute'
                with observation.bind():
                    result = await ainvoke_subagent_step(graph, instance, resume=resume,
                        approval_grants=grants, services=current_run_services())
                    snapshot = await graph.aget_state({'configurable': {'thread_id': instance['child_thread_id']}})
                observation.stage = 'finalize'
                interrupted = bool(result.get('__interrupt__'))
                if interrupted:
                    pending = self.register_live_approval(instance, result, snapshot)
                    if pending is not None:
                        self.announce_approval(observation, pending.public())
                else:
                    with self._lock:
                        instance['phase'] = 'finalize'
                    if snapshot.next or snapshot.interrupts:
                        raise PermissionMechanismError('Subagent final checkpoint still has pending work')
                observation.result(result, interrupted or None)
                instance['pending_turn'] = observe(observation.turns, observation.turns.snapshot, pending=interrupted)
                if not interrupted:
                    instance['status'] = observation.status
                    return _finish_subagent_result(result, instance, self._subagent_result_settings)
            except BaseException as error:
                if observation.status != 'stopped':
                    instance['status'] = 'failed'
                    observation.failed(error)
                instance['phase'] = 'finalize'
                raise
            finally:
                observe(observation.controls, observation.controls.close, observation.control_close_reason())
                observation.finish(False)
            if pending is not None:
                try:
                    decisions, grants = await pending.future
                finally:
                    self.release_live_approval(instance)
                identity = pending.interrupt_id
            else:
                identity, decisions, grants = result['__interrupt__'][0].id, [], None
            resume = (identity, decisions)
            instance['run_id'] = uuid4().hex
            instance['phase'] = 'execute'
            if instance['status'] != 'stopping':
                instance['status'] = 'running'

    async def settle_subagent_drivers(self, session_id, run_id):
        with self._lock:
            owned = [(key, instance) for key, instance in self._subagent_instances.items()
                     if instance['session_id'] == session_id and instance['parent_run_id'] == run_id]
            active = [(instance, instance['driver']) for _, instance in owned if 'driver' in instance]
        if active:
            self._request_scope_stop(session_id, run_id=run_id, reason='failure')
        outcomes = await asyncio.gather(*(driver for _, driver in active), return_exceptions=True)
        for (instance, driver), outcome in zip(active, outcomes):
            self.release_live_approval(instance)
            instance.pop('driver', None)
            instance['settled'] = driver.done()
            if isinstance(outcome, Exception):
                logger.error('subagent_cleanup_failed error_type=%s', safe_error_type(outcome))
    def assert_subagents_settled(self, session_id, run_id):
        with self._lock:
            owned = [(key, instance) for key, instance in self._subagent_instances.items()
                     if key[0] == session_id and instance['root_run_id'] == run_id]
            if any(not instance['settled'] or 'driver' in instance or 'approval' in instance
                   or instance['status'] not in ('completed', 'stopped', 'failed') for _, instance in owned):
                raise PermissionMechanismError('Owned subagent work has not settled')
            for key, _ in owned:
                self._subagent_instances.pop(key, None)
