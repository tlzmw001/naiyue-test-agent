"""Offline stages run inside the same durable per-command worker ownership."""
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def execute_operation(settings,ident,store):
    value=store.read(ident);kind=value['operation']
    store.save(ident,phase='running',error_code=None)
    if kind=='backup':
        from .backup import create_backup
        store.save(ident,step='backing_up_instance')
        result=create_backup(settings,ident)
        return store.save(ident,**result,step='completed')
    if kind=='restore':
        from .restore import prepare_restore,activate_restore
        if value.get('activate_requested'):return activate_restore(settings,ident,ident)
        return prepare_restore(settings,ident,value['request']['backup_id'],store)
    if kind=='gc':
        from .reclaim import prepare_gc,apply_gc
        return apply_gc(settings,ident,store) if value.get('approved_digest') else prepare_gc(settings,ident,store)
    raise CloudError('invalid_update_command')
