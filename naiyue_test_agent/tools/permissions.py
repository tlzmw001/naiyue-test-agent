"""Tool permission declarations, execution checks and one-use approval grants."""

import json
import sys
from dataclasses import dataclass
from pathlib import PurePath
from threading import Lock
from typing import Literal

from .paths import permission_path


class OneUseApprovalGrants:
    """Consume one exact tool-call grant, including its reviewed subject, once."""

    def __init__(self, approved: dict[str, object]):
        self._approved = dict(approved)
        self._lock = Lock()

    def consume(self, tool_call_id: str | None, subject: object) -> bool:
        if not tool_call_id or subject is None:
            return False
        with self._lock:
            if tool_call_id not in self._approved or self._approved[tool_call_id] != subject:
                return False
            del self._approved[tool_call_id]
            return True


class PermissionMechanismError(RuntimeError):
    """A missing or mismatched authorization must never become normal feedback."""


def execution_environment(context=None):
    if context is not None and context.scope.environment is not None:
        return context.scope.environment.public()
    return {'kind': 'host', 'platform': 'windows' if sys.platform == 'win32'
            else 'macos' if sys.platform == 'darwin' else 'linux'}


def require_file_system(context):
    from naiyue_test_agent.contracts.run_context import RuntimeContext
    from langchain_core.tools import ToolException
    if not isinstance(context, RuntimeContext):
        raise ToolException('File environment is unavailable for this invocation.')
    files, environment = context.scope.capabilities.files, context.scope.environment
    identity = context.scope.identity
    if files is None or environment is None:
        raise ToolException('File environment is unavailable. Select an execution environment before accessing files.')
    if (not identity.session_id or files.owner_session_id != identity.session_id
            or files.environment != environment or environment.project_id != identity.project_id
            or environment.kind == 'host' and (identity.execution_root is None
                or str(identity.execution_root.path) != environment.root
                or identity.execution_root.project_revision != environment.project_revision)):
        raise PermissionMechanismError('File capability differs from its execution binding')
    files.validate()
    return files



def require_process_system(context):
    """Check authority without IO; provider entry points check target health."""
    from naiyue_test_agent.contracts.run_context import RuntimeContext
    from langchain_core.tools import ToolException
    if not isinstance(context, RuntimeContext):
        raise PermissionMechanismError('Current process execution context is unavailable')
    port, environment, identity = context.scope.capabilities.processes, context.scope.environment, context.scope.identity
    if port is None or environment is None:
        raise ToolException('Process environment is unavailable. Select an execution environment before running commands.')
    files = context.scope.capabilities.files
    if (not identity.session_id or port.owner_session_id != identity.session_id
            or port.environment != environment or environment.project_id != identity.project_id
            or files is not None and files.environment != environment
            or environment.kind == 'host' and (identity.execution_root is None
                or str(identity.execution_root.path) != environment.root
                or identity.execution_root.project_revision != environment.project_revision)):
        raise PermissionMechanismError('Process capability differs from its execution binding')
    return port

def require_execution_directory(root, project_id=None):
    """Check the already selected execution identity before new filesystem work."""
    from naiyue_test_agent.execution_roots import ExecutionRootUnavailable, validate_execution_root
    from langchain_core.tools import ToolException
    try:
        validate_execution_root(root, project_id)
    except ExecutionRootUnavailable as error:
        raise ToolException(str(error)) from error
    return root.path if root is not None else None


@dataclass(frozen=True)
class ToolPermission:
    default: Literal['allow', 'ask'] = 'ask'

    def __post_init__(self):
        if self.default not in ('allow', 'ask'):
            raise ValueError('Invalid default permission')

    def prepare(self, args, context):
        root = context.scope.identity.execution_path
        return {'operation': 'execute', 'workspace': context.scope.environment.root if context.scope.environment is not None else str(root) if root is not None else None,
                'target': args.get('command', json.dumps(args, ensure_ascii=False, sort_keys=True))}

    def default_decision(self, subject):
        return self.default


@dataclass(frozen=True)
class FilePermission:
    path_field: str = 'path'
    operation: Literal['read', 'write', 'list', 'search'] = 'read'
    unavailable_message: str | None = None
    workspace_message: str | None = None

    def prepare(self, args, context):
        from langchain_core.tools import ToolException
        if context.scope.environment is None and context.scope.identity.execution_root is None and self.workspace_message is not None:
            raise ToolException(self.workspace_message)
        files = require_file_system(context)
        try:
            subject = files.prepare_target(args[self.path_field], self.operation)
        except ToolException as error:
            root = files.environment.path(files.environment.root)
            message = self.workspace_message if not root.is_absolute() else self.unavailable_message
            if message is not None:
                raise ToolException(message) from error
            raise
        subject['resource_roots'] = [str(root) for root in (context.inputs.read_resource_roots if context.inputs.read_resource_roots is not None else ())
                                     if isinstance(root, PurePath) and root.is_absolute()]
        if self.unavailable_message is not None:
            subject['unavailable_message'] = self.unavailable_message
        return subject

    def default_decision(self, subject):
        from .paths import hidden_or_private
        platform = subject.get('execution_environment', {}).get('platform')
        windows = platform == 'windows' if platform is not None else None
        root = permission_path(subject['workspace'], windows=windows)
        for key in ('requested', 'resolved'):
            if not permission_path(subject[key], windows=windows).is_relative_to(root):
                return 'ask'
        if self.operation == 'list' or not hidden_or_private(subject):
            return 'allow'
        for resource in subject.get('resource_roots', ()):
            scope = permission_path(resource, windows=windows)
            target = permission_path(subject['resolved'], windows=windows)
            requested = permission_path(subject['requested'], windows=windows)
            if scope.is_relative_to(root) and target.is_relative_to(scope) and requested.is_relative_to(scope):
                relative = (*target.relative_to(scope).parts, *requested.relative_to(scope).parts)
                if not any(part.startswith('.') for part in relative) and target.suffix.lower() not in ('.pem', '.key'):
                    return 'allow'
        return 'ask'


def permission_binding(identity, batch_id, call):
    return {'session_id': identity.session_id, 'agent_path': identity.agent_path,
            'batch_id': batch_id, 'call_id': call['id'], 'tool_name': call['name'],
            'args': call['args'], 'subject': call['subject']}


def require_tool_permit(runtime):
    context = runtime.context
    from naiyue_test_agent.contracts.run_context import RuntimeContext
    if not isinstance(context, RuntimeContext):
        raise PermissionMechanismError('Current tool execution context is unavailable')
    permit = (context.authorization.permit if context.authorization is not None else None)
    if (not isinstance(permit, dict) or permit.get('call_id') != runtime.tool_call_id
            or permit.get('session_id') != context.scope.identity.session_id
            or permit.get('agent_path') != context.scope.identity.agent_path
            or not isinstance(permit.get('subject'), dict)):
        raise PermissionMechanismError('Current tool execution permit is unavailable')
    if permit['subject'].get('execution_environment') != execution_environment(context):
        from langchain_core.tools import ToolException
        raise ToolException('Execution environment changed. Reissue the tool call for review.')
    root = context.scope.identity.execution_path
    if root is not None and permit['subject'].get('workspace') != str(root):
        raise PermissionMechanismError('Tool execution directory differs from its reviewed subject')
    execution = context.scope.identity.execution_root
    if execution is not None and execution.project_revision is not None and permit['subject'].get('project_revision') != execution.project_revision:
        raise PermissionMechanismError('Tool approval revision differs from its execution binding')
    return permit


def require_file_target(runtime, path, *, operation):
    files = require_file_system(runtime.context)
    permit = require_tool_permit(runtime)
    subject = permit['subject']
    if (subject.get('workspace') != files.environment.root or subject.get('operation') != operation
            or path != subject['args'][subject['path_field']]):
        raise PermissionMechanismError('File operation does not match its execution permit')
    return files.verify_target(subject)


def matches_session_allow(rules, subject):
    windows = subject['execution_environment']['platform'] == 'windows'
    for rule in rules:
        old_root, new_root = rule['workspace'], subject.get('workspace')
        same_workspace = (permission_path(old_root, windows=windows) == permission_path(new_root, windows=windows)
                          if old_root is not None and new_root is not None else old_root == new_root)
        if (rule['tool_name'] == subject['tool_name'] and rule['operation'] == subject['operation']
                and same_workspace
                and rule['environment'] == subject['execution_environment']):
            if 'mcp' in subject or 'mcp' in rule:
                if (rule.get('mcp') == subject.get('mcp') and rule.get('mcp') is not None
                        and rule.get('session_id') == subject.get('session_id')):
                    return True
            elif 'resolved' in subject:
                if rule.get('target') is not None and permission_path(rule['target'], windows=windows) == permission_path(subject['resolved'], windows=windows):
                    return True
            elif 'command' in subject['args']:
                if (rule.get('command') == subject['args']['command']
                        and rule.get('background', False) == subject['args'].get('background', False)):
                    return True
            elif rule.get('args') == subject['args']:
                return True
    return False


def remembered_rule(subject):
    rule = {'tool_name': subject['tool_name'], 'operation': subject['operation'],
            'workspace': subject.get('workspace'), 'environment': subject['execution_environment']}
    if 'mcp' in subject:
        rule.update(mcp=dict(subject['mcp']), session_id=subject['session_id'])
    elif 'resolved' in subject:
        rule['target'] = subject['resolved']
    elif 'command' in subject['args']:
        rule['command'] = subject['args']['command']
        if subject['args'].get('background', False):
            rule['background'] = True
    else:
        rule['args'] = subject['args']
    return rule


def candidate_access(runtime, candidate, *, operation):
    from .paths import hidden_or_private
    from .permission_rules import match_rules
    from langchain_core.tools import ToolException
    permit = require_tool_permit(runtime)
    context = runtime.context
    parent = permit['subject']
    try:
        subject = context.scope.capabilities.files.inspect_candidate(str(candidate), 'search')
    except ToolException:
        import logging
        logging.getLogger(__name__).warning('permission_candidate_unavailable')
        return False, 'unavailable_target'
    subject['operation'] = operation
    subject.update(tool_name=parent['tool_name'], args={**parent['args'], parent['path_field']: str(candidate)},
                   execution_environment=parent['execution_environment'])
    rules = context.authorization.permission_rules
    if match_rules(rules.deny, subject, deny=True):
        return False, 'deny'
    if match_rules(rules.allow, subject, deny=False) or matches_session_allow(context.authorization.session_allow, subject):
        return True, None
    windows = parent['execution_environment']['platform'] == 'windows'
    inside = permission_path(subject['resolved'], windows=windows).is_relative_to(
        permission_path(parent['resolved'], windows=windows))
    # Listing names inherits the approved directory scope, including dotfiles.
    # Content searches still require permission for private descendants.
    relative_subject = {**subject, 'workspace': parent['resolved']}
    if inside and (operation == 'list' or not hidden_or_private(relative_subject)):
        return True, None
    return False, 'unapproved_external_target' if operation == 'list' else 'unapproved_private_or_external'


def skipped_scope_message(tool_name, skipped, max_bytes):
    """Bound permission diagnostics by the tool's existing output budget."""
    if not skipped:
        return ''
    prefix = f'\n[{tool_name}: not ' + ('listed: ' if tool_name == 'ls' else 'searched: ')
    lines, size = [], len(prefix.encode('utf-8')) + 1
    omitted = f'; some of {len(skipped)} targets omitted from this report'
    for item in sorted(skipped):
        escaped = json.dumps(item, ensure_ascii=False)[1:-1]
        addition = len(escaped.encode('utf-8')) + (2 if lines else 0)
        if size + addition + len(omitted.encode('utf-8')) > max_bytes:
            return prefix + '; '.join(lines) + omitted + ']'
        lines.append(escaped)
        size += addition
    return prefix + '; '.join(lines) + ']'
