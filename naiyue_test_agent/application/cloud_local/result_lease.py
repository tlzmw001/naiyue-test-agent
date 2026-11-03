"""A receive owns a renewable server lease only while copying remote bytes."""
import math,time
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def renew(client,record):
    value=client.request('POST','/tasks/'+record.task_id+'/results/'+record.result_id+'/lease',json={'lease_id':record.receive_id})
    expiry=value.get('expires_at')
    if (value.get('result_id')!=record.result_id or value.get('lease_id')!=record.receive_id
            or type(expiry) not in (int,float) or not math.isfinite(expiry) or expiry<=time.time()):
        raise CloudError('cloud_receive_identity_mismatch',502)


def release(client,record):
    value=client.request('POST','/tasks/'+record.task_id+'/results/'+record.result_id+'/release',json={'lease_id':record.receive_id})
    if value.get('result_id')!=record.result_id or value.get('lease_id')!=record.receive_id or value.get('released') is not True:
        raise CloudError('cloud_receive_identity_mismatch',502)
