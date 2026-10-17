"""Project one scoped service object into the explicit framework context."""

from .run_services import current_run_services


def make_invocation_context(session_id, run_id, workspace_root, approval_grants, read_resource_roots, *, agent_path='root'):
    context = {'session_id': session_id, 'run_id': run_id, 'agent_path': agent_path}
    services = current_run_services()
    if services is not None:
        publisher = services.publisher
        if publisher is None or publisher.matches(session_id, run_id):
            if services.control is not None:
                context['_run_control'] = services.control
            if services.errors is not None:
                context['_failure_policy'] = services.errors
            if services.ainvoke_subagent is not None:
                context['ainvoke_subagent'] = services.ainvoke_subagent
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
