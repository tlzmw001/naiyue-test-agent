"""The lightweight optional queue is bounded even when the UI/control link is absent."""
from naiyue_test_agent.agent_runtime.events import RuntimeEvent
from naiyue_test_agent.agent_runtime.stream_events import MessageDeltaData
from test_runner_kernel import bootstrap


def event(b,text,index,*,identity='message'):
    return RuntimeEvent('message_delta',b.task_id,'a'*32,index,'2026-11-02T00:00:00+00:00',
        MessageDeltaData(identity,text,index))


def test_adjacent_deltas_merge_without_losing_index_or_identity():
    from naiyue_test_agent.cloud_runner.observations import RunnerObservations
    b=bootstrap();queue=RunnerObservations(b);queue.root_thread='root-thread'
    queue(event(b,'hello ',1));queue(event(b,'world',2))
    value=queue.drain()
    assert len(value['records'])==1 and value['records'][0]['data']['text']=='hello world'
    assert value['records'][0]['data']['index']==1 and value['records'][0]['data']['end_index']==2
    assert value['records'][0]['thread_id']=='root-thread' and value['dropped']==0
    assert queue.drain() is None


def test_queue_overflow_reports_loss_and_never_grows_without_a_connection():
    from naiyue_test_agent.cloud_runner.observations import RunnerObservations
    from naiyue_test_agent.contracts.cloud_observation import ObservationLimits
    b=bootstrap().model_copy(update={'observation_limits':ObservationLimits(queue_records=2,queue_bytes=4096)})
    queue=RunnerObservations(b);queue.root_thread='root-thread'
    for n in range(100):queue(event(b,'x',n+1,identity=str(n)))
    value=queue.drain()
    assert value['dropped']>0 and len(value['records'])<=2
    assert len(str(value).encode())<=4096


def test_invalid_optional_record_is_diagnostic_and_does_not_raise(caplog):
    from naiyue_test_agent.cloud_runner.observations import RunnerObservations
    b=bootstrap();queue=RunnerObservations(b);queue.root_thread='root-thread'
    queue(event(b,'x'*100000,1))
    value=queue.drain()
    assert value['dropped']>0 and not value['records']
    assert 'runner_observation_dropped' in caplog.text


def test_runtime_snapshot_unavailable_is_an_explicit_capture_gap():
    from naiyue_test_agent.cloud_runner.observations import RunnerObservations
    from naiyue_test_agent.agent_runtime.events import MessageSnapshotUnavailableData
    b=bootstrap();queue=RunnerObservations(b);queue.root_thread='root-thread'
    queue(RuntimeEvent('message_snapshot_unavailable',b.task_id,'a'*32,1,'2026-11-02T00:00:00+00:00',
        MessageSnapshotUnavailableData(stage='snapshot',reason='unsupported_projection')))
    value=queue.drain()
    assert value is not None and value['dropped']==1 and value['records']==[]
