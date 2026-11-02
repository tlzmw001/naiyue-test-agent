import asyncio
import pytest
from test_manual_task_commands import prepared
from test_manual_task_bindings import uid
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.errors import SessionPersistenceError


@pytest.mark.parametrize('bad',['instance','nan','infinity','boolean_generation','phase'])
def test_only_matching_finished_delete_receipt_atomically_releases_parent_guard(tmp_path,bad):
    async def run():
        manager, domain, store, sid, remote, binding = await prepared(tmp_path)
        try:
            request = dict(command_id=uid(), local_session_id=sid, expected_binding_revision=binding['revision'],
                expected_revision=binding['cloud']['revision'], expected_generation=binding['cloud']['generation'],
                preview_digest='a' * 64, acknowledge_unknown=False)
            journal = domain.repo.commands
            prior=journal.reserve(binding['binding_id'],sid,'message',dict(command_id=uid(),local_session_id=sid,
                expected_binding_revision=binding['revision'],text='private local prompt',model_id='A',draft_revision=None))
            op = journal.reserve(binding['binding_id'], sid, 'delete', request)
            payload = {k:v for k,v in request.items() if k not in ('local_session_id','expected_binding_revision')}
            payload['confirm_task_id'] = binding['task_id']
            fingerprint = digest(dict(task_id=binding['task_id'], request=payload))
            op = journal.fix(request['command_id'], sid, 'primary', payload,
                '/tasks/' + binding['task_id'] + '/delete', 'task:delete', fingerprint)
            receipt = dict(command_id=request['command_id'], resource_id=binding['task_id'], operation='task:delete',
                request_digest=fingerprint, expected_generation=request['expected_generation'], status='succeeded',
                http_status=200, created_at=1.0, updated_at=2.0, error_code=None,
                result=dict(phase='finished', tombstone=dict(instance_id=binding['instance_id'], task_id=binding['task_id'],
                    generation=request['expected_generation'], delete_command_id=request['command_id'], deleted_at=2.0)))
            wrong = dict(receipt, result={'phase':'finished','tombstone':receipt['result']['tombstone'] | {'instance_id':uid()}})
            if bad!='instance':
                wrong=dict(receipt,result=dict(receipt['result'],tombstone=dict(receipt['result']['tombstone'])))
                if bad=='phase':wrong['result']['phase']='cleaning'
                elif bad=='boolean_generation':wrong['result']['tombstone']['generation']=True
                else:wrong['result']['tombstone']['deleted_at']=float('nan') if bad=='nan' else float('inf')
            with pytest.raises(CloudError, match='cloud_delete_receipt_mismatch'):
                journal.record(request['command_id'], sid, 'primary', wrong)
            with pytest.raises(SessionPersistenceError, match='session_cloud_tasks_retained'): store.runs.delete(sid, ())
            assert journal.record(request['command_id'], sid, 'primary', receipt)['phase'] == 'succeeded'
            after = domain.repo.binding(binding['binding_id'], sid)
            assert after['retained'] is False and after['deletion'] == receipt['result']['tombstone']
            row = store.database.connection.execute('SELECT retained,body_json FROM naiyue_cloud_bindings WHERE binding_id=?',
                (binding['binding_id'],)).fetchone()
            assert row['retained'] == 0
            assert journal.record(request['command_id'], sid, 'primary', receipt)['phase'] == 'succeeded'
            old=domain.repo.operation(prior['command_id'],sid)
            assert old['error_code']=='cloud_task_deleted' and old['steps']=={}
            assert manager.commands.launch(domain,old,explicit=True)==old
            assert journal.fail(old['command_id'],sid,'unknown','late_failure')==old
            assert journal.record(old['command_id'],sid,'primary',{})==old
            with pytest.raises(CloudError,match='cloud_task_unavailable'):journal.dispatch(old['command_id'],sid,'primary')
            assert 'private local prompt' not in str(old)
            store.runs.delete(sid, ())
            await manager.close()
        finally: store.close()
    asyncio.run(run())


def test_delete_pending_or_failed_receipt_keeps_retained_guard(tmp_path):
    async def run():
        manager, domain, store, sid, remote, binding = await prepared(tmp_path)
        try:
            request = dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],
                expected_revision=binding['cloud']['revision'],expected_generation=binding['cloud']['generation'],
                preview_digest='a'*64,acknowledge_unknown=True)
            op = domain.repo.commands.reserve(binding['binding_id'],sid,'delete',request)
            for status in ('accepted','unknown'):
                receipt=dict(status=status,result={},error_code='backend_unavailable')
                domain.repo.commands.record(op['command_id'],sid,'primary',receipt)
                assert domain.repo.binding(binding['binding_id'],sid)['retained']
                with pytest.raises(SessionPersistenceError,match='session_cloud_tasks_retained'):store.runs.delete(sid,())
            await manager.close()
        finally:store.close()
    asyncio.run(run())
