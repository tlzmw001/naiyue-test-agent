"""Registry fences for shared input uses and package retirement."""
import json
from uuid import uuid4

from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .releases import require_available


def read(c, key):
    row = c.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def put(c, key, value):
    c.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (key, json.dumps(value)))


def package_state(c, ident):
    return read(c, 'input-state:'+ident)


def require_ready(c, ident):
    state = package_state(c, ident)
    if state and state['state']=='retiring':
        raise CloudError('input_retiring')
    return state


def generation(c, ident, package):
    state = require_ready(c, ident)
    if not state or state['state']=='deleted':
        state = dict(incarnation_id=str(uuid4()), state='ready', package_digest=digest(package))
        put(c, 'input-state:'+ident, state)
    return state


def candidates(c, ids):
    result=[]
    for ident in ids:
        package=read(c,'input:'+ident)
        state=package_state(c,ident)
        if package and (state is None or state['state']=='deleted'):
            state=generation(c,ident,package)
        result.append(dict(input_id=ident,incarnation_id=state['incarnation_id'] if package and state else None,
                           state='pending',accounted_bytes=package['size'] if package else 0))
    return dict(schema=1,state='pending',candidates=result,released_quota_bytes=0,
                verified_deleted_bytes=0,bytes_complete=True)


def assert_use(c, use_id, ident, purpose):
    use=read(c,'input-use:'+use_id) if use_id else None
    if not use or use['state']!='held' or (use['input_id'],use['purpose'])!=(ident,purpose):
        raise CloudError('input_use_required',409)
    state=require_ready(c,ident)
    if use['incarnation_id'] is not None and (not state or state['incarnation_id']!=use['incarnation_id']):
        raise CloudError('input_retired')
    return use


def consume(c,use_id,ident,consumer):
    if not use_id:
        return
    use=assert_use(c,use_id,ident,'associate')
    if use['origin_operation_id']!=consumer['operation_id']:
        raise CloudError('input_use_conflict')
    put(c,'input-use:'+use_id,use | dict(state='consumed',consumer=consumer))


class InputUses:
    def __init__(self,database):
        self.database=database

    def get(self,use_id):
        with self.database.transaction() as c:
            value=read(c,'input-use:'+use_id)
        if value is None:raise CloudError('input_use_not_found',404)
        return value

    def acquire(self,request,limits,max_receipts):
        value=request.model_dump()
        use_id=request.command_id;ident=request.input_id
        with self.database.transaction(write=True) as c:
            require_available(c)
            key='input-use:'+use_id
            previous=read(c,key)
            if previous:
                if any(previous[k]!=v for k,v in value.items()):raise CloudError('input_use_conflict')
                return previous
            state=require_ready(c,ident)
            package=read(c,'input:'+ident)
            if request.purpose=='recover' and package is None:raise CloudError('input_not_found',404)
            if package:state=generation(c,ident,package)
            count,held=c.execute("""SELECT count(*),coalesce(sum(json_extract(value,'$.state')='held'
                      AND json_extract(value,'$.purpose')=?),0) FROM meta WHERE key LIKE 'input-use:%'""",
                      (request.purpose,)).fetchone()
            limit=limits.max_pending if request.purpose=='associate' else limits.max_downloads
            receipts=c.execute('SELECT count(*) FROM commands').fetchone()[0]
            if count+receipts>=max_receipts or held>=limit:raise CloudError('input_use_capacity',429)
            value.update(state='held',incarnation_id=state['incarnation_id'] if package else None)
            put(c,key,value)
            return value

    def release(self,use_id):
        with self.database.transaction(write=True) as c:
            require_available(c)
            key='input-use:'+use_id
            use=read(c,key)
            if not use:raise CloudError('input_use_not_found',404)
            if use['state']=='held':
                use=use | dict(state='released')
                put(c,key,use)
            return use

    def check(self,use_id,ident,purpose):
        with self.database.transaction() as c:
            return assert_use(c,use_id,ident,purpose)


def blocker(c,ident):
    if c.execute("""SELECT 1 FROM tasks AS t WHERE json_extract(record_json,'$.lifecycle')!='deleted'
        AND (json_extract(record_json,'$.input_id')=? OR EXISTS (
        SELECT 1 FROM json_each(record_json,'$.attachments') AS a WHERE json_extract(a.value,'$.input_id')=?)) LIMIT 1""",
        (ident,ident)).fetchone():
        return 'input_task_reference'
    if c.execute("""SELECT 1 FROM meta WHERE key LIKE 'transfer:%'
        AND json_extract(value,'$.input_id')=? AND json_extract(value,'$.state') NOT IN ('committed','cancelled') LIMIT 1""",
        (ident,)).fetchone():
        return 'input_transfer_active'
    if c.execute("""SELECT 1 FROM meta WHERE key LIKE 'input-use:%'
        AND json_extract(value,'$.input_id')=? AND json_extract(value,'$.state')='held' LIMIT 1""",
        (ident,)).fetchone():
        return 'input_use_held'
    return None


def verify_legacy_cutover(database, request):
    """Persist an owner attestation after checking the listed v2 occupations."""
    with database.transaction(write=True) as c:
        key='input-legacy-cutover'
        payload=request.model_dump(mode='json')
        previous=read(c,key)
        if previous:
            if previous['request']!=payload:
                raise CloudError('input_legacy_cutover_already_verified')
            return previous
        for use_id in request.input_use_ids:
            use=read(c,'input-use:'+use_id)
            if not use or use['state']!='held':
                raise CloudError('input_legacy_cutover_use_missing')
            assert_use(c,use_id,use['input_id'],use['purpose'])
        receipt=dict(request=payload,verified=True)
        put(c,key,receipt)
        return receipt


def migrate(database):
    with database.transaction(write=True) as c:
        if c.execute('PRAGMA user_version').fetchone()[0]==3:return
        for key,raw in c.execute("SELECT key,value FROM meta WHERE key LIKE 'input:%'").fetchall():
            ident=key.split(':',1)[1]
            state=generation(c,ident,json.loads(raw))
            # v1 cannot enumerate an owner recovery between archive ranges.
            put(c,'input-state:'+ident,state|dict(legacy_unverified=True))
        for key,raw in c.execute("SELECT key,value FROM meta WHERE key LIKE 'transfer:%'").fetchall():
            transfer=json.loads(raw)
            state=package_state(c,transfer['input_id'])
            if transfer['state']=='committed' and state:
                put(c,'transfer-generation:'+key.split(':',1)[1],state['incarnation_id'])
        c.execute('PRAGMA user_version=3')
