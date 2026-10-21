"""Compile and execute an isolated child graph; application owns instances."""

from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import ToolException
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command
from langgraph.checkpoint.base import BaseCheckpointSaver
import logging

from naiyue_test_agent.model_access.messages import content_text
from . import _build_runtime_agent, InteractiveRunState
from .context_recovery import recover_input_overflow
from .session_port import SessionPort
from .errors import RuntimeErrorPolicy
from .invocation_context import make_invocation_context
from .run_services import bind_run_services, current_run_services
from naiyue_test_agent.contracts.failures import FailureDescription, attach_failure_description, safe_error_type
from .tool_execution.toolset import ToolSetMiddleware
from .async_message_observation import ainvoke_with_message_observation


class SubagentCheckpointSaver(BaseCheckpointSaver):
    """Own no storage; identify failures at this child's actual checkpoint IO."""

    def __init__(self, backend):
        super().__init__(serde=backend.serde)
        self.backend = backend

    @property
    def config_specs(self):
        return self.backend.config_specs

    def get_next_version(self, current, channel):
        return self.backend.get_next_version(current, channel)

    async def _io(self, operation, call, *args):
        try:
            return await call(*args)
        except GraphBubbleUp:
            raise
        except Exception as error:
            logging.getLogger(__name__).error('subagent_checkpoint_failed operation=%s error_type=%s',
                                              operation, safe_error_type(error))
            feedback = ToolException('Subagent checkpoint could not be confirmed. The task has ended; '
                'already completed tool effects were not rolled back. Do not retry automatically.')
            services = current_run_services()
            run_id = services.publisher.run_id if services is not None and services.publisher is not None else None
            attach_failure_description(feedback, FailureDescription('runtime', 'checkpoint_error',
                'subagent_checkpoint_failed', 'ToolException'), run_id=run_id)
            raise feedback from error

    async def aget_tuple(self, config):
        return await self._io('read', self.backend.aget_tuple, config)

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await self._io('write', self.backend.aput, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=''):
        return await self._io('writes', self.backend.aput_writes, config, writes, task_id, task_path)


class SubagentModelFailureMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        context = request.runtime.context
        context.pop('_subagent_model_failure', None)
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as error:
            policy = context.get('_failure_policy') or RuntimeErrorPolicy()
            description = policy.describe(error, source='model', model_id=context['_subagent_model_id'])
            if description.category != 'unknown':
                context['_subagent_model_failure'] = (error, description.category, description.reason)
            raise


def build_subagent_runtime(spec, *, catalog, tool_registry, checkpointer, session_port: SessionPort,
                           permission_settings, permission_reviewer):
    # Same permission/HITL/batch/authorization order as the root, using the child
    # path and role inputs. No root steering/follow-up consumers are installed.
    toolset = ToolSetMiddleware(tool_registry, session_port.resolve_active_tool_names, spec.system_prompt,
                                resolve_prompt_parts=session_port.resolve_prompt_parts)
    middleware = [recover_input_overflow]
    if spec.max_model_calls is not None:
        middleware.append(ModelCallLimitMiddleware(thread_limit=spec.max_model_calls, exit_behavior='error'))
    # Child extras keep compaction/recovery and an optional model budget;
    # the inner model failure wrapper observes only failures from the model path.
    return _build_runtime_agent(catalog.get(spec.model_id or catalog.default_model_id),
        tool_registry=tool_registry, resolve_active_tool_names=session_port.resolve_active_tool_names,
        system_prompt=spec.system_prompt, response_format=None, resolve_model=session_port.resolve_model,
        checkpointer=SubagentCheckpointSaver(checkpointer),
        steering_middleware=middleware, state_schema=InteractiveRunState, active_stop=True, observe_turns=True,
        toolset=toolset, permission_settings=permission_settings,
        resolve_permission_context=session_port.resolve_permission_context, permission_reviewer=permission_reviewer,
        model_middleware=(SubagentModelFailureMiddleware(),),
        resolve_request_configuration=session_port.resolve_request_configuration, compact_requests=True)


async def ainvoke_subagent_step(agent, instance, *, services, resume=None, approval_grants=None):
    with bind_run_services(services):
        context = make_invocation_context(instance['session_id'], instance['run_id'], instance['workspace_root'],
            approval_grants, instance['read_resource_roots'], agent_path=instance['agent_path'])
        context.update(parent_tool_call_id=instance['parent_tool_call_id'], parent_run_id=instance['parent_run_id'],
                       root_run_id=instance['root_run_id'], _subagent_model_id=instance['model_id'],
                       _human_review_messages=instance['human_messages'])
        if resume is None:
            seed = HumanMessage(content=instance['task'], id=instance['seed_id'],
                                additional_kwargs={'naiyue_input_source': 'delegated_task', 'naiyue_reply_id': instance['seed_id'], 'naiyue_input_kind': 'delegated_task'})
            input = {'messages': [seed], 'active_reply_id': instance['seed_id']}
        else:
            identity, decisions = resume
            input = Command(resume={identity: {'decisions': [{'type': item} for item in decisions]}})
        config = {'configurable': {'thread_id': instance['child_thread_id']},
                  'max_concurrency': instance['max_concurrency']}
        try:
            return await ainvoke_with_message_observation(agent, input, context=context, config=config,
                publisher=services.publisher, durability='sync')
        except ModelCallLimitExceededError as error:
            raise ToolException('Subagent model call limit reached. The task did not complete; '
                                'already executed tools were not rolled back.') from error
        except Exception as error:
            failure = context.get('_subagent_model_failure')
            if failure is not None and failure[0] is error:
                feedback = ToolException(f'Subagent model request failed ({failure[1]}/{failure[2]}). '
                                         'No final report is available.')
                attach_failure_description(feedback, FailureDescription('model', failure[1], failure[2],
                    'ToolException'), run_id=instance['run_id'])
                raise feedback from error
            raise


def _finish_subagent_result(result, instance, settings):
    if result.get('__interrupt__'):
        raise ValueError('An interrupted child has no final report')
    if result.get('run_stopped'):
        raise ToolException('Subagent stopped before completing its task.')
    messages = result.get('messages', [])
    last = messages[-1] if messages else None
    if not isinstance(last, AIMessage) or last.tool_calls:
        raise ToolException('Subagent finished without a final report.')
    text = content_text(last.content)
    if not text.strip():
        raise ToolException('Subagent finished without a text report.')
    pieces, size = [], 0
    for line in text.splitlines(keepends=True):
        encoded = line.encode('utf-8')
        if len(pieces) == settings.max_result_lines:
            break
        if size + len(encoded) > settings.max_result_bytes:
            if not pieces:
                pieces.append(encoded[:settings.max_result_bytes].decode('utf-8', errors='ignore'))
            break
        pieces.append(line)
        size += len(encoded)
    report = ''.join(pieces)
    truncated = report != text
    if truncated:
        report += '\n[subagent: report truncated by configured line/byte budget; full child history remains in its checkpoint]'
    usages = [message.usage_metadata for message in messages if isinstance(message, AIMessage)]
    usage = ({key: sum(item[key] for item in usages) for key in ('input_tokens', 'output_tokens', 'total_tokens')}
             if usages and all(item is not None for item in usages) else None)
    return report, {'agent_path': instance['agent_path'], 'agent': instance['role'],
                    'model_id': instance['model_id'], 'status': 'completed',
                    'usage': usage, 'truncated': truncated}
