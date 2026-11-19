"""Read the local business dependencies of a frozen cloud input."""
import json
from pathlib import Path
import re

from .cache_cleanup_files import CleanupIssue

INPUT_ID = re.compile('[0-9a-f]{64}')


def input_dependencies(connection, cache, project, run, *, legacy_checks=None, import_links=None):
    packages, imports, sources, bindings = {}, {}, {}, {}
    create_commands = {}

    def rows(sql):
        data = connection.execute(sql + ' LIMIT ?', (run.remaining + 1,)).fetchall()
        run.check(len(data), path=cache)
        return data

    def protect(ident, reason):
        if not isinstance(ident, str) or not INPUT_ID.fullmatch(ident):
            raise CleanupIssue('metadata_unavailable', '输入依赖缺少有效的包身份', skipped=False)
        packages.setdefault(ident, reason)

    def source(handle, sid, reason):
        captured, saved_sid = sources[handle]
        if saved_sid != sid:
            raise ValueError('source session mismatch')
        ident = captured['input_id']
        protect(ident, reason)
        if Path(captured['directory']) != cache / 'inputs' / ident:
            raise ValueError('input directory mismatch')
        if receipt := captured.get('source_receipt'):
            path = Path(receipt)
            if path.parent != cache / 'imports':
                raise ValueError('input receipt mismatch')
            imports[path.name] = reason
        return ident

    try:
        for handle, sid, pid, raw in rows('SELECT source_handle,session_id,project_id,body_json FROM naiyue_cloud_sources'):
            if pid != project:
                raise ValueError('source project mismatch')
            captured = json.loads(raw)['captured']
            sources[handle] = captured, sid
            if import_links is not None and captured.get('source_receipt'):
                receipt = Path(captured['source_receipt'])
                if receipt.parent == cache / 'imports':
                    import_links.setdefault(receipt.name, set()).add(captured['input_id'])
        for bid, sid, pid, retained, raw in rows('SELECT binding_id,session_id,project_id,retained,body_json FROM naiyue_cloud_bindings'):
            binding = json.loads(raw)
            if pid != project or retained != binding['retained']:
                raise ValueError('binding scope mismatch')
            bindings[bid] = binding, sid
            if binding.get('input_id') and binding.get('preparation') not in ('prepared', 'cancelled'):
                create_commands[bid] = binding.get('create_command_id')
            for ident in binding.get('pending_attachment_releases', {}).values():
                protect(ident, '附件取消仍待确认')
            for row in binding.get('local_attachments', {}).values():
                phase = row['phase']
                if phase not in ('uploading', 'failed', 'cancelling', 'cancelled', 'ready', 'attached'):
                    raise ValueError('unknown attachment phase')
                active = phase in ('uploading', 'failed', 'cancelling') or (
                    phase == 'cancelled' and not row.get('input_use_released'))
                if active and row.get('input_id'):
                    protect(row['input_id'], '附件仍可重试或待取消')
                if active and row.get('source_handle') and source(row['source_handle'], sid,
                        '附件仍可重试或待取消') != row.get('input_id'):
                    raise ValueError('attachment input mismatch')
        for cid, bid, sid, raw in rows('SELECT command_id,binding_id,session_id,body_json FROM naiyue_cloud_operations'):
            op = json.loads(raw)
            if op['kind'] != 'create':
                continue
            handle = op['request'].get('source_handle')
            binding, saved_sid = bindings[bid]
            if saved_sid != sid:
                raise ValueError('operation session mismatch')
            if bid in create_commands and cid == create_commands[bid]:
                if not handle:
                    raise ValueError('missing operation source')
                del create_commands[bid]
            if handle and (op['phase'] not in ('prepared', 'cancelled')
                           or op.get('may_have_task') and not binding.get('task_id')):
                if source(handle, sid, '任务创建仍待完成或核实') != binding.get('input_id'):
                    raise ValueError('operation input mismatch')
        for bid in create_commands:
            protect(bindings[bid][0]['input_id'], '任务创建记录缺失或不匹配，无法确认输入依赖')

        business_protected = set(packages)
        for cid, sid, pid, raw in rows('SELECT command_id,session_id,project_id,body_json FROM naiyue_cloud_deliveries'):
            delivery = json.loads(raw)
            if delivery['action'] == 'files' or delivery['phase'] not in ('executing', 'partial', 'unknown'):
                continue
            run.check()
            found = connection.execute('SELECT body_json FROM naiyue_cloud_receives WHERE receive_id=?'
                                       ' AND session_id=? AND project_id=?',
                                       (delivery['receive_id'], sid, project)).fetchone()
            if pid != project or found is None:
                raise ValueError('missing delivery receive')
            receive = json.loads(found[0])
            binding, saved_sid = bindings[receive['binding_id']]
            ident = receive['input_id']
            if saved_sid != sid or binding.get('input_id') != ident:
                raise ValueError('delivery binding mismatch')
            reason = f'旧交付 {cid} 的恢复仍依赖此输入'
            protect(ident, reason)
            if legacy_checks is not None and ident not in business_protected:
                legacy_checks.setdefault(ident, []).append((cid, receive.get('input_package')))
            if handle := receive.get('source_handle'):
                if source(handle, sid, reason) != ident:
                    raise ValueError('delivery source mismatch')
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise CleanupIssue('metadata_unavailable', '无法核验输入缓存的业务依赖', skipped=False) from error
    return packages, imports
