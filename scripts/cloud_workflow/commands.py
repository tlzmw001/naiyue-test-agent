"""One immutable request per logical operation, even after an unknown HTTP ACK."""
import time
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def command(client,journal,key,ident,path,body,*,timeout,poll):
    wanted=dict(path=path,body=dict(body,command_id=ident))
    old=journal.value['commands'].get(key)
    if old is None:
        journal.value['commands'][key]=wanted;journal.save()
    elif old!=wanted:raise CloudError('probe_request_changed')
    begin=time.monotonic();deadline=begin+timeout
    try:value=client.request('GET','/commands/'+ident)
    except CloudError as error:
        if (error.code,error.status)!=('command_not_found',404):raise
        value=client.request('POST',path,json=wanted['body'])
        value=value.get('receipt',value)
        journal.value.setdefault('ack_seconds',{})[key]=time.monotonic()-begin;journal.save()
    while True:
        if value.get('command_id')!=ident:raise CloudError('probe_receipt_identity')
        status=value.get('status')
        if status=='succeeded':return value
        if status in ('unknown','failed'):raise CloudError('probe_command_'+status)
        if status not in ('accepted','running'):raise CloudError('probe_receipt_invalid')
        if time.monotonic()>=deadline:raise CloudError('probe_command_timeout')
        time.sleep(min(poll,max(0,deadline-time.monotonic())))
        value=client.request('GET','/commands/'+ident)


def wait(client,path,predicate,*,timeout,poll):
    deadline=time.monotonic()+timeout
    while True:
        value=client.request('GET',path)
        if predicate(value):return value
        if value.get('phase') in ('failed','abandoned') or value.get('lifecycle') in ('failed','deleted'):
            raise CloudError('probe_resource_failed')
        if time.monotonic()>=deadline:raise CloudError('probe_resource_timeout')
        time.sleep(min(poll,max(0,deadline-time.monotonic())))
