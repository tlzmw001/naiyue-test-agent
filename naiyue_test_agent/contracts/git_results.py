"""Finite result receipts, tied to the installed input and environment."""
import re
from .git_inputs import GitInputError, input_id, input_path, object_id

GIT_RESULT_CODES=frozenset({
    'git_result_invalid_request','git_result_invalid_selection','git_result_invalid_receipt',
    'git_result_binding_mismatch','git_result_budget','git_result_not_descendant','git_result_cancelled',
    'git_result_invalid_transfer','git_result_corrupt','git_result_invalid_bundle','git_result_layout_changed',
    'git_result_ref_conflict','git_result_file_not_found','git_result_not_found','git_result_unconfirmed',
    'git_result_busy','git_result_project_changed','git_result_io_failed',
})


def result_request(value):
    if not isinstance(value,dict) or set(value)!={'result_id','input_id','original_head','input_snapshot','include_untracked'}:
        raise GitInputError('git_result_invalid_request')
    input_id(value['result_id']);input_id(value['input_id'])
    object_id(value['original_head']);object_id(value['input_snapshot'])
    selected=value['include_untracked']
    if (not isinstance(selected,list) or any(not isinstance(p,str) for p in selected)
            or len(set(selected))!=len(selected)):
        raise GitInputError('git_result_invalid_selection')
    for path in selected:input_path(path)
    return value


def result_receipt(value):
    keys={'result_id','binding_id','project_id','session_id','input_id','original_head','input_snapshot',
        'output_commit','bundle_size','bundle_sha256','excluded'}
    if not isinstance(value,dict) or set(value)!=keys:
        raise GitInputError('git_result_invalid_receipt')
    input_id(value['result_id']);input_id(value['input_id'])
    for key in ('original_head','input_snapshot','output_commit'):object_id(value[key])
    for key in ('binding_id','project_id','session_id'):
        if not isinstance(value[key],str) or not 1<=len(value[key])<=128:
            raise GitInputError('git_result_invalid_receipt')
    if (type(value['bundle_size']) is not int or value['bundle_size']<=0
            or not isinstance(value['bundle_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['bundle_sha256'])
            or not isinstance(value['excluded'],list)):
        raise GitInputError('git_result_invalid_receipt')
    for item in value['excluded']:
        if not isinstance(item,dict) or set(item)!={'path','reason'} or item['reason'] not in ('untracked','ignored'):
            raise GitInputError('git_result_invalid_receipt')
        input_path(item['path'])
    return value


def result_ref(ident):
    return 'refs/naiyue/tasks/'+input_id(ident)
