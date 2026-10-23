"""Project one scoped service object into the explicit framework context."""

from .run_services import current_run_services


def make_invocation_context(session_id, run_id, workspace_root, approval_grants, read_resource_roots, *, agent_path='root'):
    context = {'session_id': session_id, 'run_id': run_id, 'agent_path': agent_path}
    services = current_run_services()
    if services is not None:
        publisher = services.publisher
        if publisher is None or publisher.matches(session_id, run_id):
            context['project_id'] = services.project_id
            if services.execution_root is not None:
                if services.execution_root.path != workspace_root:
                    raise ValueError('Execution scope does not match invocation path')
                context['execution_root'] = services.execution_root
            if services.archive_root is not None:
                context['archive_root'] = services.archive_root
            if services.control is not None:
                context['_run_control'] = services.control
            if services.errors is not None:
                context['_failure_policy'] = services.errors
            if services.ainvoke_subagent is not None:
                context['ainvoke_subagent'] = services.ainvoke_subagent
            if services.alist_subagents is not None and agent_path == 'root':
                context['alist_subagents'] = services.alist_subagents
            if services.process_tasks is not None:
                context['_process_tasks'] = services.process_tasks
            if services.task_queries is not None:
                context['_task_queries'] = services.task_queries
            if services.history_query is not None:
                context['aquery_history'] = services.history_query
            if services.memory_query is not None:
                context['aquery_memory'] = services.memory_query
            notices = services.background_notices
            if (notices is not None and agent_path == 'root' and notices.session_id == session_id
                    and notices.workspace_root == workspace_root and notices.run_id == run_id):
                context['_background_notices'] = notices
            if publisher is not None:
                context['_run_event_publisher'] = publisher
                for name, observer in (('_turn_observer', services.turns),
                                       ('_control_observer', services.controls),
                                       ('_context_observer', services.context_operations),
                                       ('_tool_progress_observer', services.tool_progress)):
                    if observer is not None and observer.publisher is publisher:
                        context[name] = observer
    if workspace_root is not None:
        context['workspace_root'] = workspace_root
    if approval_grants is not None:
        context['approval_grants'] = approval_grants
    if read_resource_roots:
        context['read_resource_roots'] = read_resource_roots
    return context
