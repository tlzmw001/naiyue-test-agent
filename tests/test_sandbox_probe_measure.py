"""Measured fields must distinguish payload bytes and wire bytes."""
import asyncio
import pytest


def test_target_must_be_running_and_explicitly_owned_for_exercises():
    from scripts.sandbox_probe.target import validate_container
    data={'Id':'a'*64,'State':{'Running':True},'Config':{'Labels':{
        'naiyue.binding':'B','naiyue.project':'P','naiyue.session':'S'}}}
    assert validate_container(data)['naiyue.binding']=='B'
    with pytest.raises(ValueError):validate_container(data,owned=True)
    data['Config']['Labels']['naiyue.probe']='spec141'
    assert validate_container(data,owned=True)['naiyue.session']=='S'
    data['State']['Running']=False
    with pytest.raises(ValueError):validate_container(data)


def test_failed_probe_writes_no_fabricated_zero_latency(tmp_path):
    from scripts.sandbox_probe.__main__ import save_report
    import json
    path=tmp_path/'report.json'
    save_report(path,{'status':'failed','error_code':'probe_timeout','stages':{}})
    assert json.loads(path.read_text())['stages']=={}
    with pytest.raises(FileExistsError):save_report(path,{})


def test_measurement_uses_actual_envd_not_only_ping():
    from scripts.sandbox_probe.measure import latency_samples
    calls=[]
    class Channel:
        async def request(self,operation,params):calls.append((operation,params));return {}
    result=asyncio.run(latency_samples(Channel(),'hello',{},samples=3,concurrency=1))
    assert len(result)==3 and all(value>=0 for value in result)
    assert calls==[('hello',{})]*3


def test_atomic_report_never_overwrites_existing_file(tmp_path):
    from scripts.sandbox_probe.__main__ import save_report
    target=tmp_path/'existing.json';target.write_text('original')
    with pytest.raises(FileExistsError):save_report(target,{'status':'passed'})
    assert target.read_text()=='original'
