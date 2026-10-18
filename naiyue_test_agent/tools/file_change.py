"""Bounded, opt-in file change artifacts; no ambient reads during projection."""

import logging
from .edit_diff import _bounded, _unified


def read_feedback_allowed(runtime, path):
    from .permissions import FilePermission, matches_session_allow
    from .permission_rules import match_rules
    context = runtime.context
    subject = {**context['_execution_permit']['subject'], 'operation': 'read',
               'tool_name': 'read', 'path_field': 'path', 'args': {'path': path}}
    rules = context['_permission_rules']
    if match_rules(rules.deny, subject, deny=True):
        return False
    return bool(match_rules(rules.allow, subject, deny=False)
        or matches_session_allow(context['_session_allow'], subject)
        or context.get('_permission_mode') == 'BypassApprovals'
        or FilePermission().default_decision(subject) == 'allow')


def write_feedback(target, content, settings, *, read_allowed, display_path):
    artifact = dict(type='file_change', version=1, operation='write', path=display_path,
        firstChangedLine=None, diff='', preview='', diff_truncated=False, preview_truncated=False,
        patch=None, patch_omitted=True, unavailable_reason=None)
    if target.exists() and not read_allowed:
        return {**artifact, 'unavailable_reason': 'read_not_allowed'}
    if target.exists():
        try:
            with target.open('rb') as source:
                old = source.read(settings.max_diff_input_bytes + 1)
        except OSError as error:
            logging.getLogger(__name__).warning('write_feedback_unavailable error_type=%s', type(error).__name__)
            return {**artifact, 'unavailable_reason': 'read_failed'}
        if len(old) > settings.max_diff_input_bytes:
            return {**artifact, 'unavailable_reason': 'input_budget'}
        try:
            before = old.decode('utf-8')
        except UnicodeError:
            return {**artifact, 'unavailable_reason': 'not_utf8'}
        if '\x00' in before:
            return {**artifact, 'unavailable_reason': 'binary'}
    else:
        before = ''
    if len(content.encode('utf-8')) > settings.max_diff_input_bytes:
        return {**artifact, 'unavailable_reason': 'input_budget'}
    body = ''.join(_unified(before, content, 0, 0))
    diff, truncated = _bounded(body, settings.max_diff_bytes)
    preview, preview_truncated = _bounded(content, settings.max_preview_bytes)
    first = next((i + 1 for i, (a, b) in enumerate(zip(before.split('\n'), content.split('\n'))) if a != b),
                 min(len(before.split('\n')), len(content.split('\n'))))
    patch = f'--- a/{display_path}\t\n+++ b/{display_path}\t\n' + body
    representable = not any(ord(c) < 32 or ord(c) == 127 for c in display_path)
    complete = representable and '\r' not in before.replace('\r\n', '') + content.replace('\r\n', '')
    complete &= len(patch.encode('utf-8')) <= settings.max_patch_bytes
    return {**artifact, 'firstChangedLine': first if before != content else None, 'diff': diff, 'diff_truncated': truncated,
        'preview': preview, 'preview_truncated': preview_truncated,
        'patch': patch if complete else None, 'patch_omitted': not complete}


def change_projection(artifact, *, preview_bytes=4096):
    if (not isinstance(artifact, dict) or artifact.get('type') != 'file_change'
            or artifact.get('version') != 1 or artifact.get('operation') not in ('write', 'edit')):
        return None
    if not isinstance(artifact.get('path'), str) or len(artifact['path']) > 4096:
        return None
    line = artifact.get('firstChangedLine')
    if line is not None and (type(line) is not int or line < 1):
        return None
    if any(key in artifact and type(artifact[key]) is not bool for key in ('diff_truncated', 'preview_truncated', 'patch_omitted')):
        return None
    reason = artifact.get('unavailable_reason')
    if reason is not None and (not isinstance(reason, str) or len(reason) > 80):
        return None
    value = {key: artifact.get(key) for key in ('type', 'version', 'operation', 'path', 'firstChangedLine',
        'diff_truncated', 'preview_truncated', 'patch_omitted', 'unavailable_reason')}
    for key in ('diff', 'preview'):
        text = artifact.get(key, '')
        if not isinstance(text, str):
            return None
        value[key], trimmed = _bounded(text, preview_bytes)
        value[key + '_truncated'] = bool(value.get(key + '_truncated') or trimmed)
    value['detail_available'] = True
    return value
