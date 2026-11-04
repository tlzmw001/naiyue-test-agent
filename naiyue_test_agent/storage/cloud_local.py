"""Project-owned delegation records; admission and parent deletion share one DB."""
import json
import time
from uuid import uuid4

from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.errors import SessionPersistenceError
from .queries import session_row

REQUIRED={
    'naiyue_cloud_sources':'source_handle session_id project_id body_json',
    'naiyue_cloud_targets':'profile_id instance_id body_json',
    'naiyue_cloud_bindings':'binding_id session_id project_id retained body_json',
    'naiyue_cloud_operations':'command_id binding_id session_id body_json',
}


def initialize(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0]>=21:return
    statements='''
        CREATE TABLE IF NOT EXISTS naiyue_cloud_sources(
            source_handle TEXT PRIMARY KEY,session_id TEXT NOT NULL,project_id TEXT NOT NULL,
            body_json TEXT NOT NULL,FOREIGN KEY(session_id) REFERENCES naiyue_sessions(session_id));
        CREATE TABLE IF NOT EXISTS naiyue_cloud_targets(
            profile_id TEXT NOT NULL,instance_id TEXT NOT NULL,body_json TEXT NOT NULL,
            PRIMARY KEY(profile_id,instance_id));
        CREATE TABLE IF NOT EXISTS naiyue_cloud_bindings(
            binding_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,project_id TEXT NOT NULL,
            retained INTEGER NOT NULL CHECK(retained IN (0,1)),body_json TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES naiyue_sessions(session_id));
        CREATE INDEX IF NOT EXISTS naiyue_cloud_parent ON naiyue_cloud_bindings(session_id,retained);
        CREATE TABLE IF NOT EXISTS naiyue_cloud_operations(
            command_id TEXT PRIMARY KEY,binding_id TEXT NOT NULL,session_id TEXT NOT NULL,body_json TEXT NOT NULL,
            FOREIGN KEY(binding_id) REFERENCES naiyue_cloud_bindings(binding_id));
    '''
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        for statement in statements.split(';'):
            if statement.strip():connection.execute(statement)
        connection.execute('UPDATE naiyue_store_info SET version=21')


def check_deletion(connection,session_id=None):
    from .cloud_delivery import check_deletion as check_delivery
    check_delivery(connection,session_id)
    args=() if session_id is None else (session_id,)
    query=('SELECT 1 FROM naiyue_cloud_bindings WHERE retained=1 LIMIT 1' if session_id is None
        else 'SELECT 1 FROM naiyue_cloud_bindings WHERE retained=1 AND session_id=? LIMIT 1')
    if connection.execute(query,args).fetchone():
        raise SessionPersistenceError('session_cloud_tasks_retained')


def purge(connection,session_id):
    from .cloud_delivery import purge as purge_deliveries
    purge_deliveries(connection,session_id)
    from .cloud_receive import purge as purge_receives
    purge_receives(connection,session_id)
    connection.execute('DELETE FROM naiyue_cloud_operations WHERE session_id=?',(session_id,))
    connection.execute('DELETE FROM naiyue_cloud_bindings WHERE session_id=?',(session_id,))
    connection.execute('DELETE FROM naiyue_cloud_sources WHERE session_id=?',(session_id,))


def _read(connection,table,value,session_id,code):
    # Table/column identifiers are fixed internal constants, never request values.
    queries={
        'naiyue_cloud_sources':'SELECT body_json FROM naiyue_cloud_sources WHERE source_handle=? AND session_id=?',
        'naiyue_cloud_bindings':'SELECT body_json FROM naiyue_cloud_bindings WHERE binding_id=? AND session_id=?',
        'naiyue_cloud_operations':'SELECT body_json FROM naiyue_cloud_operations WHERE command_id=? AND session_id=?',
    }
    row=connection.execute(queries[table],(value,session_id)).fetchone()
    if row is None:raise CloudError(code,404)
    return json.loads(row[0])


class LocalTaskRepo:
    def __init__(self,database):
        self.database=database
        from .cloud_local_commands import LocalTaskCommands
        self.commands=LocalTaskCommands(database)

    def add_source(self,session_id,project_id,captured):
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            if session_row(c,session_id)['project_id']!=project_id:raise CloudError('cloud_project_mismatch',403)
            value=dict(source_handle=str(uuid4()),local_session_id=session_id,local_project_id=project_id,
                captured=captured,created_at=time.time())
            c.execute('INSERT INTO naiyue_cloud_sources VALUES(?,?,?,?)',
                (value['source_handle'],session_id,project_id,json.dumps(value)))
            return value

    def source(self,handle,session_id):
        with self.database.transaction(write=False,error_code='cloud_local_read_failed') as c:
            return _read(c,'naiyue_cloud_sources',handle,session_id,'cloud_source_not_found')

    def binding(self,binding_id,session_id):
        with self.database.transaction(write=False,error_code='cloud_local_read_failed') as c:
            return _read(c,'naiyue_cloud_bindings',binding_id,session_id,'cloud_binding_not_found')

    def operation(self,command_id,session_id):
        with self.database.transaction(write=False,error_code='cloud_local_read_failed') as c:
            return _read(c,'naiyue_cloud_operations',command_id,session_id,'cloud_operation_not_found')

    def list(self,session_id,*,offset=0,limit=50):
        if not 0<=offset<=1000000 or not 1<=limit<=100:raise CloudError('invalid_cloud_page',422)
        with self.database.transaction(write=False,error_code='cloud_local_read_failed') as c:
            session_row(c,session_id)
            rows=c.execute('''SELECT body_json FROM naiyue_cloud_bindings WHERE session_id=? AND retained=1
                ORDER BY CASE WHEN json_extract(body_json,'$.cloud.active_run_id') IS NOT NULL THEN 0
                WHEN json_extract(body_json,'$.preparation') NOT IN ('prepared','cancelled','failed') THEN 1 ELSE 2 END,
                json_extract(body_json,'$.created_at') DESC,binding_id LIMIT ? OFFSET ?''',(session_id,limit,offset)).fetchall()
            return [json.loads(r[0]) for r in rows]

    def reserve(self,project_id,request,target):
        sid=request['local_session_id'];command_id=request['command_id']
        fingerprint=digest(request|{'instance_id':target['instance_id'],'local_project_id':project_id})
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            if session_row(c,sid)['project_id']!=project_id:raise CloudError('cloud_project_mismatch',403)
            old=c.execute('SELECT body_json FROM naiyue_cloud_operations WHERE command_id=?',(command_id,)).fetchone()
            if old:
                operation=json.loads(old[0])
                if operation['request_digest']!=fingerprint:raise CloudError('cloud_command_conflict')
                return {'operation':operation,'binding':_read(c,'naiyue_cloud_bindings',operation['binding_id'],sid,'cloud_binding_not_found')}
            source=_read(c,'naiyue_cloud_sources',request['source_handle'],sid,'cloud_source_not_found')
            profile=request['profile_id'];instance=target['instance_id']
            row=c.execute('SELECT body_json FROM naiyue_cloud_targets WHERE profile_id=? AND instance_id=?',(profile,instance)).fetchone()
            mapping=json.loads(row[0]) if row else dict(command_id=str(uuid4()),name=target['project_name'])
            if row is None:c.execute('INSERT INTO naiyue_cloud_targets VALUES(?,?,?)',(profile,instance,json.dumps(mapping)))
            now=time.time();bid=str(uuid4())
            binding=dict(network_id=target.get('network_id'),binding_id=bid,local_project_id=project_id,local_session_id=sid,profile_id=profile,
                instance_id=instance,cloud_project_id=None,task_id=None,input_id=source['captured']['input_id'],
                create_command_id=command_id,template_id=request['template_id'],created_at=now,revision=1,preparation='reserved',cloud=None,retained=True)
            op=dict(command_id=command_id,binding_id=bid,local_session_id=sid,kind='create',request=request,
                request_digest=fingerprint,phase='reserved',steps={'project':mapping,'task':{'command_id':str(uuid4()),'network_id':target.get('network_id')},
                    'environment':{'command_id':str(uuid4())}},may_have_task=False,cancel_requested=False,
                created_at=now,updated_at=now,error_code=None)
            c.execute('INSERT INTO naiyue_cloud_bindings VALUES(?,?,?,?,?)',(bid,sid,project_id,1,json.dumps(binding)))
            c.execute('INSERT INTO naiyue_cloud_operations VALUES(?,?,?,?)',(command_id,bid,sid,json.dumps(op)))
            return {'operation':op,'binding':binding}

    def update(self,command_id,session_id,*,phase=None,steps=None,binding=None,error_code=None,dispatch_task=False):
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            op=_read(c,'naiyue_cloud_operations',command_id,session_id,'cloud_operation_not_found')
            value=_read(c,'naiyue_cloud_bindings',op['binding_id'],session_id,'cloud_binding_not_found')
            if value.get('deletion'):raise CloudError('cloud_task_unavailable')
            if op['cancel_requested'] and phase not in ('cancelled','unknown'):
                raise CloudError('cloud_preparation_cancelled')
            if dispatch_task:op['may_have_task']=True
            if phase is not None:op['phase']=value['preparation']=phase
            if steps is not None:op['steps']=steps
            if binding:
                if set(binding)-{'cloud_project_id','task_id','cloud'}:raise ValueError('invalid_binding_update')
                for key in ('cloud_project_id','task_id'):
                    if value[key] is not None and key in binding and value[key]!=binding[key]:raise CloudError('cloud_binding_changed')
                value.update(binding)
            op['error_code']=error_code;op['updated_at']=time.time();value['revision']+=1
            c.execute('UPDATE naiyue_cloud_operations SET body_json=? WHERE command_id=?',(json.dumps(op),command_id))
            c.execute('UPDATE naiyue_cloud_bindings SET body_json=? WHERE binding_id=?',(json.dumps(value),value['binding_id']))
            return {'operation':op,'binding':value}

    def cancel(self,command_id,session_id,*,settled=True):
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            op=_read(c,'naiyue_cloud_operations',command_id,session_id,'cloud_operation_not_found')
            if op['phase']=='prepared':raise CloudError('cloud_preparation_already_finished')
            value=_read(c,'naiyue_cloud_bindings',op['binding_id'],session_id,'cloud_binding_not_found')
            if value.get('deletion'):raise CloudError('cloud_task_unavailable')
            op.update(cancel_requested=True,phase='cancelled',updated_at=time.time())
            value.update(preparation='cancelled',revision=value['revision']+1,retained=op['may_have_task'] or not settled)
            c.execute('UPDATE naiyue_cloud_operations SET body_json=? WHERE command_id=?',(json.dumps(op),command_id))
            c.execute('UPDATE naiyue_cloud_bindings SET body_json=?,retained=? WHERE binding_id=?',
                (json.dumps(value),int(value['retained']),value['binding_id']))
            return {'operation':op,'binding':value}

    def fail(self,command_id,session_id,phase,code):
        op=self.operation(command_id,session_id)
        if op['cancel_requested'] or op['phase']=='prepared':return
        try:self.update(command_id,session_id,phase=phase,error_code=code)
        except CloudError as error:
            if error.code!='cloud_preparation_cancelled':raise

    def reject_task(self,command_id,session_id):
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            op=_read(c,'naiyue_cloud_operations',command_id,session_id,'cloud_operation_not_found')
            value=_read(c,'naiyue_cloud_bindings',op['binding_id'],session_id,'cloud_binding_not_found')
            if value['task_id'] is not None:raise CloudError('cloud_task_identity_mismatch')
            op.update(may_have_task=False,updated_at=time.time())
            c.execute('UPDATE naiyue_cloud_operations SET body_json=? WHERE command_id=?',(json.dumps(op),command_id))

    def settle_cancel(self,command_id,session_id):
        op=self.operation(command_id,session_id)
        if op['cancel_requested']:self.cancel(command_id,session_id,settled=True)

    def record_task(self,command_id,session_id,task,*,steps=None):
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            op=_read(c,'naiyue_cloud_operations',command_id,session_id,'cloud_operation_not_found')
            value=self._cache(c,op['binding_id'],session_id,task)
            if steps is not None:
                op['steps']=steps;op['updated_at']=time.time()
                c.execute('UPDATE naiyue_cloud_operations SET body_json=? WHERE command_id=?',(json.dumps(op),command_id))
            return {'operation':op,'binding':value}

    def cache_task(self,binding_id,session_id,task):
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            return self._cache(c,binding_id,session_id,task)

    def _cache(self,c,binding_id,session_id,task):
        value=_read(c,'naiyue_cloud_bindings',binding_id,session_id,'cloud_binding_not_found')
        if value.get('deletion'):return value
        from naiyue_test_agent.contracts.cloud_local_tasks import checked_task
        checked=checked_task(task,value).model_dump(mode='json')
        old=value['cloud']
        if old is not None and (checked['generation'],checked['revision'])<(old['generation'],old['revision']):return value
        recovered=(value['preparation'] in ('failed','unknown') and checked['lifecycle']=='ready'
            and checked['environment_phase']=='backend_ready' and checked['runner_phase'] in ('ready','waiting_model'))
        if old==checked and not recovered:return value
        if recovered:value['preparation']='prepared'
        value.update(task_id=checked['task_id'],cloud=checked,revision=value['revision']+1)
        c.execute('UPDATE naiyue_cloud_bindings SET body_json=? WHERE binding_id=?',(json.dumps(value),binding_id))
        return value
