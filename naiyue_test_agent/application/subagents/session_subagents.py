"""Application-owned role definitions shared by this application's sessions."""

import logging
import asyncio
from naiyue_test_agent.contracts.run_context import RunIdentity
import sys
from contextvars import Context
from uuid import uuid4

from langchain_core.tools import ToolException

from naiyue_test_agent.contracts.failures import safe_error_type
from naiyue_test_agent.tools.subagent import SubAgentInput
from naiyue_test_agent.tools.permissions import PermissionMechanismError, permission_binding
from naiyue_test_agent.agent_runtime.run_control import RunControl
from naiyue_test_agent.agent_runtime.events import AgentEventSource
from naiyue_test_agent.agent_runtime.observation import observe
from naiyue_test_agent.agent_runtime.run_scope import current_run_scope
from naiyue_test_agent.agent_runtime.tool_execution.permissions import collect_human_messages
from naiyue_test_agent.agent_runtime.subagents import ainvoke_subagent_step, _finish_subagent_result
from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking
from naiyue_test_agent.application.sessions.session_approval import announce_approval
from naiyue_test_agent.application.observability.run_events import RunObservation
from .subagent_history import subagent_identity
from . import subagent_records


from naiyue_test_agent.application.sessions.session_state import SessionRuntime
from naiyue_test_agent.application.sessions.session_controls import ControlQueue
from naiyue_test_agent.application.sessions.session_approval import ApprovalBook


logger = logging.getLogger('naiyue_test_agent.application')


class SubagentRegistry:
    """Borrow one runtime to own its child instances, drivers and cleanup."""

    def __init__(self, runtime: SessionRuntime, *, controls: ControlQueue, approvals: ApprovalBook,
                 catalog, persistence, registered_subagents, progress_settings, result_settings,
                 mcp_resources, archive_root):
        self._runtime, self.session_id = runtime, runtime.session_id
        self._controls, self._approvals = controls, approvals
        self._catalog, self._persistence = catalog, persistence
        self._registered_subagents = registered_subagents
        self._progress_settings, self._subagent_result_settings = progress_settings, result_settings
        self._mcp_resources, self._archive_root = mcp_resources, archive_root

    async def asubagent_references(self):
        return await run_blocking(self._persistence.storage.subagents.get_subagent_references, self.session_id)

    async def alist_subagents(self, *, parent_context, parent_state, limit=None, before=None):
        from .subagent_queries import annotate, bounded_list
        def require_current():
            if (parent_context.scope.identity.agent_path != 'root'
                    or parent_context.scope.identity.session_id != self.session_id or self._runtime.run is None
                    or self._runtime.run.run_id != parent_context.scope.identity.run_id
                    or self._runtime.state.execution_path != parent_context.scope.identity.execution_path):
                raise PermissionMechanismError('Subagent list requires its current root run and workspace')
        with self._runtime.lock:
            require_current()
            run_id = self._runtime.run.run_id
        owned = await self.asubagent_references()
        rows = await run_blocking(self._persistence.storage.conversation.chat_subagents, self.session_id)
        rows = annotate(rows, owned, self._registered_subagents(), self._catalog, run_id)
        with self._runtime.lock:
            require_current()
        return bounded_list(rows, owned, self._subagent_result_settings, limit=limit, before=before)

    def prepare_subagent_instance(self, agent, task, *, role, parent_context, parent_state, parent_tool_call_id, agent_id=None):
        if parent_context.scope.identity.agent_path != 'root':
            raise ToolException('Only one level of subagent delegation is supported.')
        session_id = self.session_id
        SubAgentInput(agent=agent, task=task, agent_id=agent_id)
        if parent_context.scope.identity.session_id != session_id:
            raise PermissionMechanismError('Subagent belongs to a different session')
        batch = parent_state.get('tool_execution', {})
        call = next((item for item in batch.get('calls', []) if item['id'] == parent_tool_call_id), None)
        permit = (parent_context.authorization.permit if parent_context.authorization is not None else None)
        expected = {'agent': agent, 'task': task}
        if call is not None and 'agent_id' in call['args']:
            expected['agent_id'] = agent_id
        if (call is None or call['name'] != 'subagent' or call['args'] != expected
                or call['args'].get('agent_id') != agent_id
                or permit != permission_binding(parent_context.scope.identity, batch.get('batch_id'), call)):
            raise PermissionMechanismError('Subagent parent call does not match its execution permit')
        delegation = subagent_identity(session_id, batch['batch_id'], parent_tool_call_id)
        identity = agent_id or delegation
        with self._runtime.lock:
            session, run = self._runtime.state, self._runtime.run
            if run is None or run.run_id != parent_context.scope.identity.run_id:
                raise PermissionMechanismError('Subagent requires the current active parent run')
            if run.stop_requested:
                raise ToolException('Subagent was not started because its owning run is stopping.')
            if role is None:
                raise ToolException('Unknown subagent role. Choose a registered role.')
            spec = role['spec']
            if parent_context.scope.identity.execution_path != session.execution_path:
                raise PermissionMechanismError('Subagent workspace does not match its parent')
            path = f'root/{agent}/{identity}'
            key = path
            if delegation in run.subagent_calls:
                raise PermissionMechanismError('Subagent call has already been started; replay is not allowed')
            previous = run.subagent_instances.get(key)
            if previous is not None and not previous['settled']:
                raise ToolException('Subagent instance is busy; wait for its current delegation to finish.')
            run.subagent_calls.add(delegation)
            instance = dict(session_id=session_id, role=agent, agent_id=identity, agent_path=path,
                delegation_id=delegation, continuing=agent_id is not None, parent_batch_id=batch['batch_id'],
                parent_reply_id=parent_state.get('active_reply_id') or run.reply_id or '',
                child_thread_id=f'subagent:{identity}', seed_id=f'subagent-seed:{delegation}',
                parent_tool_call_id=parent_tool_call_id, parent_run_id=run.run_id,
                root_run_id=(parent_context.scope.identity.root_run_id if parent_context.scope.identity.root_run_id is not None else run.run_id), run_id=uuid4().hex,
                model_id=spec.model_id or session.model_id, tool_names=spec.tool_names,
                status='running', phase='execute', settled=False, task=task, workspace_root=run.execution_root.path if run.execution_root else None,
                project_id=run.project_id, execution_root=run.execution_root,
                read_resource_roots=(parent_context.inputs.read_resource_roots if parent_context.inputs.read_resource_roots is not None else ()),
                max_concurrency=(parent_context.inputs.max_concurrency if parent_context.inputs.max_concurrency is not None else 4), control=RunControl(),
                publisher=parent_context.scope.observation.publisher, pending_turn=None,
                errors=parent_context.scope.errors, human_messages=collect_human_messages(parent_state['messages']))
            self._runtime.run.subagent_instances[key] = instance
            return role['agent'], instance

    async def ainvoke_subagent(self, agent, task, *, role, parent_context, parent_state, parent_tool_call_id, agent_id=None):
        graph, instance = self.prepare_subagent_instance(agent, task, role=role, parent_context=parent_context,
            parent_state=parent_state, parent_tool_call_id=parent_tool_call_id, agent_id=agent_id)
        driver = None
        try:
            await subagent_records.admit(graph, instance, role['spec'], runtime=self._runtime,
                catalog=self._catalog, sessions=self._persistence.storage.sessions,
                subagents=self._persistence.storage.subagents, result_settings=self._subagent_result_settings)
            with self._runtime.lock:
                self._runtime.require_subagent(instance)
                if self._runtime.run.stop_requested or instance['control'].requested:
                    raise ToolException('Subagent was not started because its owning run is stopping.')
                driver = asyncio.create_task(self._subagent_steps(graph, instance), context=Context())
                instance['driver'] = driver
            return await asyncio.shield(driver)
        except asyncio.CancelledError:
            self._controls.request_scope_stop(run_id=instance['root_run_id'],
                                     agent_path=instance['agent_path'], reason='failure')
            try:
                if driver is not None:
                    await await_settled(driver)
                else:
                    instance['status'] = 'stopped'
            except BaseException as error:
                logger.info('subagent_cancel_settled error_type=%s', safe_error_type(error))
            raise
        except BaseException:
            if instance['status'] != 'stopped':
                instance['status'] = 'failed'
            raise
        finally:
            primary = sys.exception()
            with self._runtime.lock:
                instance['phase'] = 'finalize'
            cleanup_error = None
            try:
                if self._mcp_resources is not None:
                    await self._mcp_resources.release_instance(self.session_id, instance['agent_path'])
            except BaseException as error:
                logger.error('subagent_mcp_cleanup_failed error_type=%s', safe_error_type(error))
                if primary is None:
                    instance['status'] = 'failed'
                    cleanup_error = error
            finally:
                self._approvals.release_live_approval(instance)
                instance.pop('driver', None)
                try:
                    await await_settled(subagent_records.settle(instance, subagents=self._persistence.storage.subagents))
                except BaseException as error:
                    instance['status'] = 'failed'
                    if primary is None and cleanup_error is None:
                        raise
                    logger.error('subagent_settle_secondary_failed error_type=%s', safe_error_type(error))
                finally:
                    instance['settled'] = driver is None or driver.done()
            if cleanup_error is not None:
                raise cleanup_error

    async def _subagent_steps(self, graph, instance):
        resume, grants = None, None
        while True:
            source = AgentEventSource(instance['agent_path'], instance['agent_path'].rsplit('/', 1)[-1],
                instance['run_id'], instance['parent_run_id'], instance['parent_tool_call_id'])
            instance['publisher_view'] = instance['publisher'].for_agent(source)
            observation = RunObservation(instance['publisher_view'],
                'approval_resume' if resume else 'message', progress_settings=self._progress_settings,
                stop_control=instance['control'], runtime_errors=instance['errors'])
            observe(observation.turns, observation.turns.restore, instance['pending_turn'])
            pending = None
            try:
                observation.start()
                observation.stage = 'execute'
                with observation.bind(identity=RunIdentity(self.session_id, instance['run_id'], instance['agent_path'],
                        project_id=instance['project_id'], execution_root=instance['execution_root'],
                        parent_tool_call_id=instance['parent_tool_call_id'], parent_run_id=instance['parent_run_id'], root_run_id=instance['root_run_id']), archive_root=self._archive_root,
                                      history_query=getattr(self._persistence, 'aquery_history', None) if instance['project_id'] is not None else None,
                                      memory_query=getattr(self._persistence, 'aquery_memory', None) if instance['project_id'] is not None else None):
                    result = await ainvoke_subagent_step(graph, instance, resume=resume,
                        approval_grants=grants, scope=current_run_scope())
                    snapshot = await graph.aget_state({'configurable': {'thread_id': instance['child_thread_id']}})
                observation.stage = 'finalize'
                interrupted = bool(result.get('__interrupt__'))
                if interrupted:
                    pending = self._approvals.register_live_approval(instance, result, snapshot)
                    if pending is not None:
                        announce_approval(observation, pending.public())
                else:
                    with self._runtime.lock:
                        instance['phase'] = 'finalize'
                    if snapshot.next or snapshot.interrupts:
                        raise PermissionMechanismError('Subagent final checkpoint still has pending work')
                observation.result(result, interrupted or None)
                instance['pending_turn'] = observe(observation.turns, observation.turns.snapshot, pending=interrupted)
                if not interrupted:
                    instance['status'] = observation.status
                    instance['final_snapshot'] = snapshot
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
                    self._approvals.release_live_approval(instance)
                identity = pending.interrupt_id
            else:
                identity, decisions, grants = result['__interrupt__'][0].id, [], None
            resume = (identity, decisions)
            instance['run_id'] = uuid4().hex
            instance['phase'] = 'execute'
            if instance['status'] != 'stopping':
                instance['status'] = 'running'

    async def settle_subagent_drivers(self, run_id):
        with self._runtime.lock:
            owned = [(key, instance) for key, instance in self._runtime.run.subagent_instances.items()
                     if instance['parent_run_id'] == run_id]
            active = [(instance, instance['driver']) for _, instance in owned if 'driver' in instance]
        if active:
            self._controls.request_scope_stop(run_id=run_id, reason='failure')
        outcomes = await asyncio.gather(*(driver for _, driver in active), return_exceptions=True)
        for (instance, driver), outcome in zip(active, outcomes):
            self._approvals.release_live_approval(instance)
            instance.pop('driver', None)
            instance['settled'] = driver.done()
            if isinstance(outcome, Exception):
                logger.error('subagent_cleanup_failed error_type=%s', safe_error_type(outcome))

    def assert_subagents_settled(self, run_id):
        with self._runtime.lock:
            owned = [(key, instance) for key, instance in self._runtime.run.subagent_instances.items()
                     if instance['root_run_id'] == run_id]
            if any(not instance['settled'] or 'driver' in instance or 'approval' in instance
                   or instance['status'] not in ('completed', 'stopped', 'failed') for _, instance in owned):
                raise PermissionMechanismError('Owned subagent work has not settled')
            for key, _ in owned:
                self._runtime.run.subagent_instances.pop(key, None)

    def subagent_status(self, agent_path):
        with self._runtime.lock:
            instance = self._runtime.run.subagent_instances[agent_path]
            return {'status': instance['status'], 'settled': instance['settled']}

    def child_thread_ids(self):
        with self._runtime.lock:
            return tuple(instance['child_thread_id'] for instance in self._runtime.run.subagent_instances.values()) if self._runtime.run else ()
