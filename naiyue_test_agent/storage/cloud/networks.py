"""Network switches and run admission share CT1; interrupted switches stay closed."""
import json
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_networks import NetworkChange


def read_network(connection,ident):
    row=connection.execute('SELECT value FROM meta WHERE key=?',('network:'+ident,)).fetchone()
    return json.loads(row[0]) if row else dict(revision=0,slots={},switching=None,last_command=None)


def require_network(connection,ident,revision):
    state=read_network(connection,ident)
    if state['switching']:raise CloudError('cloud_network_switching',409)
    if state['revision']!=revision:raise CloudError('cloud_network_changed',409)
    if not any(v['enabled'] for v in state['slots'].values()):raise CloudError('cloud_network_unavailable',409)


class NetworkRepository:
    def __init__(self,database):self.database=database

    def read(self,ident):
        with self.database.transaction() as connection:return read_network(connection,ident)

    def _save(self,connection,ident,value):
        connection.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            ('network:'+ident,json.dumps(value)))
        return value

    def begin(self,ident,change):
        change=NetworkChange.model_validate(change);payload=change.model_dump(mode='json')
        with self.database.transaction(write=True) as connection:
            state=read_network(connection,ident)
            if state['switching']==payload:return state
            if state['last_command']==payload:return state
            if state['switching']:raise CloudError('cloud_network_switching',409)
            if state['revision']!=change.expected_revision:raise CloudError('cloud_network_changed',409)
            busy=connection.execute('''SELECT COUNT(*) FROM tasks WHERE json_extract(record_json,'$.network_id')=?
                AND json_extract(record_json,'$.lifecycle')!='deleted' AND (
                json_extract(record_json,'$.active_run_id') IS NOT NULL
                OR json_extract(record_json,'$.environment_operation_id') IS NOT NULL
                OR json_extract(record_json,'$.control_operation_id') IS NOT NULL
                OR json_extract(record_json,'$.artifact_operation_id') IS NOT NULL
                OR json_extract(record_json,'$.lifecycle') IN ('deleting','reconciling')
                OR (json_extract(record_json,'$.environment_phase')!='absent' AND json_extract(record_json,'$.runner_phase')!='ready')
                OR EXISTS(SELECT 1 FROM json_each(json_extract(tasks.record_json,'$.reclaim_blockers')) WHERE value!='result_pending'))''',
                (ident,)).fetchone()[0]
            if busy:raise CloudError('cloud_network_busy',409)
            state['switching']=payload
            return self._save(connection,ident,state)

    def finish(self,ident,command_id):
        with self.database.transaction(write=True) as connection:
            state=read_network(connection,ident)
            if not state['switching']:
                if state['last_command'] and state['last_command']['command_id']==command_id:return state
                raise CloudError('cloud_network_changed',409)
            change=state['switching']
            if change['command_id']!=command_id:raise CloudError('cloud_network_changed',409)
            state['slots'][change['slot']]=change['target'];state['revision']+=1
            state['last_command']=change;state['switching']=None
            return self._save(connection,ident,state)

    def cancel(self,ident,command_id):
        with self.database.transaction(write=True) as connection:
            state=read_network(connection,ident)
            if state.get('cancelled_command')==command_id:return state
            if not state['switching'] or state['switching']['command_id']!=command_id:
                raise CloudError('cloud_network_changed',409)
            state['switching']=None;state['revision']+=1;state['cancelled_command']=command_id
            return self._save(connection,ident,state)
