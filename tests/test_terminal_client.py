"""The terminal adapter owns network IO only, with actual service execution."""
import asyncio
from langchain_core.messages import AIMessage
from test_async_application import StreamingToolModel
from test_service_tcp import tcp_service


def test_terminal_client_uses_network_loop_and_shared_service(monkeypatch, tmp_path):
    from naiyue_test_agent.cli.terminal_client import TerminalClient
    async def scenario():
        async with tcp_service(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='answer')])) as (_, options, _, _):
            def terminal():
                client = TerminalClient(options)
                try:
                    client.bind('chats')
                    sid = client.request('POST', '/sessions', json={'model_id': 'A'})['session_id']
                    result = client.run(sid, 'question')
                    assert [m['content'] for m in result['messages']] == ['question', 'answer']
                    assert not hasattr(client, '_app')
                finally:
                    client.close()
            await asyncio.to_thread(terminal)
    asyncio.run(scenario())


def test_completed_run_cache_does_not_retain_every_full_history(monkeypatch, tmp_path):
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    from test_service_ownership import options
    from terminal_service_helpers import TerminalService
    model = StreamingToolModel(replies=[AIMessage(content=f'answer {i}') for i in range(8)])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    with TerminalService(options(tmp_path)) as server:
        client = server.client()
        try:
            client.lease(tmp_path)
            sid = client.request('POST', '/sessions', json={'model_id': 'A'})['session_id']
            for i in range(8):
                result = client.run(sid, f'question {i}')
                assert result['messages'][-1]['content'] == f'answer {i}'
            cached = client._submit(lambda: asyncio.sleep(0, result=[t.result() for t in client._streams.values() if t.done()]))
            assert len(cached) <= 1, 'Finished streams must not retain each round of complete history'
            assert sum(len(v['messages']) for v in cached) <= 16
            assert len(client._cursors) <= 1
        finally:
            client.close()


def test_two_terminal_processes_share_one_owner_and_can_control_same_run(monkeypatch, tmp_path):
    import subprocess
    import sys
    import json
    from threading import Event
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    from test_service_ownership import options
    from terminal_service_helpers import TerminalService
    entered = Event()
    model = StreamingToolModel(replies=[], entered=entered, release=asyncio.Event())
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    script = '''
import sys,json
from uuid import uuid4
from naiyue_test_agent.cli.terminal_client import TerminalClient
from naiyue_test_agent.service.settings import ServiceOptions
client=TerminalClient(ServiceOptions.model_validate_json(sys.argv[1]))
try:
    client.bind('chats')
    if len(sys.argv)==2:
        sid=client.request('POST','/sessions',json={'model_id':'A'})['session_id']
        rid=client.request('POST',f'/sessions/{sid}/runs',json={'request_id':str(uuid4()),'text':'wait'})['run_id']
        print(json.dumps([sid,rid,client._http.instance_id]),flush=True)
        sys.stdin.readline()
    else:
        sid,rid,instance=sys.argv[2:]
        assert client._http.instance_id==instance
        assert client.active_run(sid)==rid
        client.control(sid,'stop')
        assert client.wait(sid,rid)['run']['status']=='stopped'
        print('same-owner-stop-confirmed',flush=True)
finally:
    client.close()
'''
    with TerminalService(options(tmp_path)) as server:
        first = subprocess.Popen([sys.executable, '-c', script, server.options.model_dump_json()],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            sid, rid, instance = json.loads(first.stdout.readline())
            assert entered.wait(5)
            second = subprocess.run([sys.executable, '-c', script, server.options.model_dump_json(), sid, rid, instance],
                capture_output=True, text=True, timeout=20)
            assert second.returncode == 0, second.stderr
            assert 'same-owner-stop-confirmed' in second.stdout
            assert first.poll() is None and len(model.seen) == 1
        finally:
            stdout, stderr = first.communicate('\n', timeout=20)
            assert first.returncode == 0, stderr
