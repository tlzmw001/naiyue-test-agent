"""Wire identity and bounds are checked independently of provider success."""
from io import BytesIO
from dataclasses import replace
import pytest
from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.contracts.sandbox import SandboxError,PROTOCOL_VERSION
from naiyue_test_agent.envd.protocol import encode,receive,check_request


def request(environment):
    return dict(protocol=PROTOCOL_VERSION,request_id='Q',environment=environment.public(),session_id='S',operation='hello',params={})


@pytest.mark.parametrize('field,value',[('protocol',PROTOCOL_VERSION+1),('session_id','OTHER'),('environment',{}),('request_id','')])
def test_request_identity_is_not_inferred_from_path(tmp_path,field,value):
    environment=EnvironmentRef('B','sandbox','P',str(tmp_path),'linux')
    value_map=request(environment);value_map[field]=value
    with pytest.raises(SandboxError):check_request(value_map,environment,'S')


def test_protocol_does_not_accept_unbounded_or_non_object_frames():
    for raw in (b'{}',b'[]\n',b'x'*100+b'\n',b'not-json\n'):
        with pytest.raises(SandboxError):receive(BytesIO(raw),64)
    with pytest.raises(SandboxError):encode({'text':'large'*100},64)


def test_dispatcher_rejects_unknown_operations_and_owner_without_io(tmp_path):
    from naiyue_test_agent.envd.dispatch import FileDispatcher
    environment=EnvironmentRef('B','sandbox','P',str(tmp_path),'linux')
    dispatcher=FileDispatcher(environment,'S')
    value=request(environment);value['operation']='shell'
    with pytest.raises(SandboxError,match='env_operation_unsupported'):dispatcher.dispatch(value)
    value['operation']='read_resource';value['params']={'path':str(tmp_path/'file'),'max_bytes':-1}
    with pytest.raises(SandboxError,match='env_message_too_large'):dispatcher.dispatch(value)
    value['session_id']='other'
    with pytest.raises(SandboxError,match='env_binding_mismatch'):dispatcher.dispatch(value)


def test_completed_write_with_oversized_response_is_unknown(monkeypatch,tmp_path):
    from naiyue_test_agent.envd.operations import EnvironmentOperations
    from naiyue_test_agent.sandbox_settings import SandboxSettings
    from naiyue_test_agent.envd.files import EnvdFiles
    from naiyue_test_agent.tools.settings import WriteSettings
    environment=EnvironmentRef('B','sandbox','P',str(tmp_path),'linux')
    subject=EnvdFiles(environment,'S').prepare_target('large.txt','write')
    subject.update(args={'path':'large.txt'},path_field='path',tool_name='write')
    payload=dict(subject=subject,content='a'*25000,read_allowed=True,settings=WriteSettings().model_dump(mode='json'))
    with EnvironmentOperations(environment,'S',tmp_path/'state',SandboxSettings(max_message_bytes=65536)) as operations:
        operations.submit('a'*32,'write',payload,{})
        operations.wait('a'*32,5)
        row=operations.journal.get('a'*32)
        assert row['state']=='unknown'
        response=row['result']
    assert (tmp_path/'large.txt').read_text()=='a'*25000
    assert response['ok'] is False
    assert response['error']['code']=='env_operation_unknown'
