"""Required archive failure boundaries and real Runner-to-owner delivery."""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent.contracts.cloud_history import HistoryLimits,message_frames
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CommandReceipt
from naiyue_test_agent.contracts.cloud_events import EventBatch
from naiyue_test_agent.storage.database import Database
from naiyue_test_agent.storage.cloud.history import CloudHistory
from test_history_projection import record,append,page


@pytest.mark.parametrize('failure',['pending_bytes','pending_count','parts','cross_run','digest'])
def test_pending_budgets_identity_and_rebuild_failure_preserve_confirmed_data(tmp_path,failure):
    limits=HistoryLimits(max_message_bytes=20000,max_pending_bytes=4096,max_pending_messages=1,max_parts=100)
    frames=list(message_frames(record(AIMessage(id='big',content='x'*10000)),frame_bytes=4096,limits=limits))
    with Database.open_cloud(tmp_path/'history.sqlite',profile='history',initialize=True) as db:
        store=CloudHistory(db,'b'*32,initialize=True,limits=limits)
        if failure=='digest':
            append(store,[record(AIMessage(id='ok',content='saved'))])
            with db.transaction(write=True) as c:c.execute("UPDATE events SET digest=? WHERE seq=1",('0'*64,))
            with pytest.raises(CloudError,match='history_journal_corrupt'):store.rebuild()
            assert page(store)['messages'][0]['message_id']=='ok'
            return
        append(store,frames[:1]);assert page(store)['messages']==[]
        if failure=='pending_bytes':
            payload=frames[1]|{'text':'x'*5000};code='history_pending_capacity'
        elif failure=='pending_count':
            payload=next(message_frames(record(AIMessage(id='other',content='x'*10000)),frame_bytes=4096,limits=limits))
            code='history_pending_capacity'
        elif failure=='parts':payload=frames[1]|{'index':101};code='history_too_many_parts'
        else:
            batch=EventBatch(task_id='b'*32,generation=1,epoch='10000000-0000-0000-0000-000000000001',
                events=[dict(seq=2,source_generation=1,run_id='c'*32,kind='history_record',payload=frames[1])])
            with pytest.raises(CloudError,match='history_part_gap'):store.append(batch)
            assert page(store)['coverage_seq']==1
            return
        with pytest.raises(CloudError,match=code):append(store,[payload],start=2)
        assert page(store)['coverage_seq']==1 and page(store)['messages']==[]


def test_archive_read_timeout_releases_owner_transaction(tmp_path,monkeypatch,caplog):
    from naiyue_test_agent.storage.cloud import history_reads
    with Database.open_cloud(tmp_path/'history.sqlite',profile='history',initialize=True) as db:
        store=CloudHistory(db,'b'*32,initialize=True)
        append(store,[record(AIMessage(id='a',content='saved'))])
        with monkeypatch.context() as patch:
            ticks=iter([0.0]+[10.0]*100);patch.setattr(history_reads,'monotonic',lambda:next(ticks))
            with pytest.raises(CloudError,match='history_read_timeout'):page(store)
        assert 'cloud_archive_read_timeout' in caplog.text
        assert not db.connection.in_transaction and page(store)['messages'][0]['message_id']=='a'


def test_real_runner_long_archive_is_readable_after_runner_closes(tmp_path):
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from naiyue_test_agent.cloud_runner.kernel import open_runner
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.storage.cloud.lifecycle import LifecycleRepository
    from cloud_helpers import settings
    from test_history_ingress import setup_task
    from test_runner_kernel import bootstrap,request
    from test_async_application import StreamingToolModel
    async def run(runtime,task,token):
        b=bootstrap().model_copy(update={'task_id':task.task_id,'project_id':task.project_id,'max_frame_bytes':4096})
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        cmd=request();content='测试结果'*5000
        receipt=CommandReceipt(command_id=cmd.command_id,resource_id=task.task_id,operation='run:start',
            expected_generation=1,request_digest='f'*64,status='running',http_status=202,created_at=1.0,updated_at=1.0,
            result={'command':cmd.model_dump()})
        LifecycleRepository(runtime.database).reserve(task,task,receipt,runtime.settings)
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':StreamingToolModel(replies=[AIMessage(content=content)])})) as runner:
            assert (await runner.execute(cmd))['status']=='completed'
            while pending:=runner.journal.pending():
                batch=EventBatch(task_id=task.task_id,generation=1,epoch=b.state_epoch,events=pending)
                ack=runtime.append_events(token,batch.model_dump())
                assert runtime.append_events(token,batch.model_dump())==ack
                runner.journal.ack(ack['last_seq'])
        result=runtime.archive.page(task.task_id)
        assert [m['type'] for m in result['messages']]==['human','ai']
        assert result['messages'][0]['owner_input'] is True and result['messages'][0]['boundary']
        message=result['messages'][1];offset=0;received=''
        while True:
            detail=runtime.archive.detail(task.task_id,message['id'],thread_id=result['thread']['thread_id'],
                revision=message['revision'],offset=offset,limit=1000)
            received+=detail['text']
            if detail['next_offset'] is None:break
            offset=detail['next_offset']
        assert received==content
        return task.task_id
    config=settings(tmp_path,max_frame_bytes=4096)
    with open_cloud_runtime(config) as runtime:
        task,token=setup_task(runtime);task_id=asyncio.run(run(runtime,task,token))
    with open_cloud_runtime(config) as runtime:
        assert len(runtime.archive.page(task_id)['messages'])==2
