"""Build trusted invocation context shared by sync and async entry points."""

from .events import current_run_event_publisher
from .turn_observation import current_turn_observer
from .control_observation import current_control_observer
from .context_operation_observation import current_context_operation_observer


def make_invocation_context(session_id, run_id, workspace_root, approval_grants, read_resource_roots):
    context = {'session_id': session_id, 'run_id': run_id}
    publisher = current_run_event_publisher()
    if publisher is not None and publisher.matches(session_id, run_id):
        context['_run_event_publisher'] = publisher
        for name, getter in (('_turn_observer', current_turn_observer),
                             ('_control_observer', current_control_observer),
                             ('_context_observer', current_context_operation_observer)):
            observer = getter()
            if observer is not None and observer.publisher is publisher:
                context[name] = observer
    if workspace_root is not None:
        context['workspace_root'] = workspace_root
    if approval_grants is not None:
        context['approval_grants'] = approval_grants
    if read_resource_roots:
        context['read_resource_roots'] = read_resource_roots
    return context
