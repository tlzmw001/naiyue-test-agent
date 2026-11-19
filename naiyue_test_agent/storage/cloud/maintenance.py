"""Admission checks and maintenance blockers use the same registry transaction."""
import json
from .releases import meta


def blockers(db):
    result=[]
    for row in db.execute('SELECT task_id,record_json FROM tasks'):
        task=json.loads(row[1])
        if (task.get('active_run_id') or task.get('environment_operation_id') or task.get('artifact_operation_id')
                or task.get('control_operation_id') or task.get('reclaim_blockers')
                or task.get('lifecycle') in ('starting','stopping','reconciling','deleting')):
            result.append(dict(kind='task',id=row[0]))
    for row in db.execute("SELECT command_id,record_json FROM commands WHERE json_extract(record_json,'$.status') IN ('accepted','running','unknown') OR (json_extract(record_json,'$.operation')='result:capture' AND json_extract(record_json,'$.result.phase')='deleting')"):
        result.append(dict(kind='command',id=row[0]))
    for key,value in db.execute("SELECT key,value FROM meta WHERE key LIKE 'transfer:%' OR key LIKE 'maintenance-lease:%'"):
        record=json.loads(value)
        if key.startswith('maintenance-lease:') or record.get('state') not in ('committed','cancelled'):
            result.append(dict(kind='transfer',id=key))
    for row in db.execute("""SELECT DISTINCT json_extract(value,'$.retiring_command_id') FROM meta
        WHERE key LIKE 'input-state:%' AND json_extract(value,'$.state')='retiring' ORDER BY 1"""):
        result.append(dict(kind='input_cleanup',id=row[0]))
    return result


def continuing_cleanup(db,ident,*,input_id=None):
    if input_id is not None:
        state=meta(db,'input-state:'+input_id,{})
        return state.get('state')=='retiring' and state.get('retiring_command_id')==ident
    row=db.execute('SELECT record_json FROM commands WHERE command_id=?',(ident,)).fetchone()
    if row is None:return False
    value=json.loads(row[0])
    return value['operation']=='result:capture' and value['result'].get('phase')=='deleting'
