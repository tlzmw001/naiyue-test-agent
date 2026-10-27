"""Real envd ownership with controlled lost acknowledgements and process output."""
from threading import Event
import time
import pytest
from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.sandbox_settings import SandboxSettings


def test_lost_write_ack_does_not_repeat_the_side_effect(tmp_path):
    from naiyue_test_agent.envd.operations import EnvironmentOperations
    env=EnvironmentRef('B','sandbox','P',str(tmp_path/'code'),'linux');(tmp_path/'code').mkdir()
    with EnvironmentOperations(env,'S',tmp_path/'state',SandboxSettings()) as operations:
        entered,release=Event(),Event();calls=[]
        def effect(request,control=None):
            calls.append(request['params']);entered.set();assert release.wait(5)
            return ['written',{}]
        operations.files.dispatch=effect
        first=operations.submit('a'*32,'write',{'subject':{},'content':'x'}, {})
        assert entered.wait(5)
        second=operations.submit('a'*32,'write',{'subject':{},'content':'x'}, {})
        assert first['operation_id']==second['operation_id']
        release.set();operations.wait('a'*32,5)
        third=operations.submit('a'*32,'write',{'subject':{},'content':'x'}, {})
        assert len(calls)==1 and third['state']=='completed'
        assert third['result']['value']==['written',{}]


def test_real_process_output_and_confirmed_stop(tmp_path):
    from naiyue_test_agent.envd.operations import EnvironmentOperations
    from naiyue_test_agent.tools.settings import BashSettings,ProcessSettings
    env=EnvironmentRef('B','sandbox','P',str(tmp_path/'code'),'linux');(tmp_path/'code').mkdir()
    with EnvironmentOperations(env,'S',tmp_path/'state',SandboxSettings()) as operations:
        payload=dict(name='bash',command='printf once',timeout=None,
            settings=BashSettings().model_dump(mode='json'),process_settings=ProcessSettings().model_dump(mode='json'))
        operations.submit('b'*32,'shell',payload,{})
        operations.wait('b'*32,10)
        row=operations.journal.get('b'*32)
        assert row['state']=='completed' and row['result']['value']['returncode']==0
        assert operations.journal.output('b'*32,0,100)['data']==b'once'
        payload['command']='printf started; while :; do sleep 1; done'
        operations.submit('c'*32,'shell',payload,{})
        deadline=time.monotonic()+10
        while not operations.journal.output('c'*32,0,100)['data']:
            assert time.monotonic()<deadline
            operations.wait('c'*32,.05)
        row=operations.stop('c'*32)
        assert row['state']=='stopped' and row['result']['value']['process_reaped']


def test_unknown_operation_never_claims_a_confirmed_stop(tmp_path):
    from naiyue_test_agent.envd.operations import EnvironmentOperations
    from naiyue_test_agent.contracts.sandbox import SandboxError
    env=EnvironmentRef('B','sandbox','P',str(tmp_path/'code'),'linux');(tmp_path/'code').mkdir()
    with EnvironmentOperations(env,'S',tmp_path/'state',SandboxSettings()) as operations:
        operations.journal.reserve('d'*32,'shell',{}, {})
        operations.journal.finish('d'*32,'unknown')
        with pytest.raises(SandboxError,match='env_stop_unconfirmed'):operations.stop('d'*32)


def test_argv_uses_configured_path_and_preserves_literal_arguments(tmp_path):
    from naiyue_test_agent.envd.operations import EnvironmentOperations
    from naiyue_test_agent.tools.settings import AITestSettings
    code=tmp_path/'code';code.mkdir();binary=tmp_path/'custom';binary.mkdir()
    executable=binary/'only-in-configured-path'
    executable.write_text('#!/bin/sh\nprintf "%s" "$1"\n');executable.chmod(0o700)
    settings=AITestSettings(executable=executable.name,environment={'path_prepend':[str(binary)]})
    env=EnvironmentRef('B','sandbox','P',str(code),'linux')
    with EnvironmentOperations(env,'S',tmp_path/'state',SandboxSettings()) as operations:
        operations.submit('e'*32,'argv',dict(args=['literal; $(never execute)'],timeout=None,
            settings=settings.model_dump(mode='json'),process_settings=None),{})
        operations.wait('e'*32,10)
        assert operations.journal.get('e'*32)['state']=='completed'
        assert operations.journal.output('e'*32,0,100)['data']==b'literal; $(never execute)'


def test_file_commit_uncertainty_is_not_recorded_as_known_failure(tmp_path):
    from naiyue_test_agent.envd.operations import EnvironmentOperations
    from naiyue_test_agent.tools.write import WriteOutcomeUnknown
    code=tmp_path/'code';code.mkdir();env=EnvironmentRef('B','sandbox','P',str(code),'linux')
    with EnvironmentOperations(env,'S',tmp_path/'state',SandboxSettings()) as operations:
        def uncertain(*_a,**_k):raise WriteOutcomeUnknown('File write outcome is unknown')
        operations.files.dispatch=uncertain
        operations.submit('f'*32,'write',{'subject':{}},{})
        operations.wait('f'*32,5)
        assert operations.journal.get('f'*32)['state']=='unknown'
