"""Report collisions and private exception text must fail before/leak no IO."""
import json
import sys

import pytest


def arguments(path):
    return ['probe','--domain','127.0.0.1:1','--api-key-env','S00_TEST_KEY',
        '--image','fixture','--image-id','sha256:fixture','--network','fixture',
        '--fixture-root','/fixture','--output',str(path)]


def test_existing_report_does_not_start_any_backend_operation(tmp_path,monkeypatch):
    from scripts import verify_cloud_backend as probe
    path=tmp_path/'report.json';path.write_text('preserve')
    calls=[]
    async def scenario(*args):calls.append(True)
    monkeypatch.setattr(probe,'scenario',scenario)
    monkeypatch.setattr(sys,'argv',arguments(path))
    with pytest.raises(FileExistsError):probe.main()
    assert calls==[] and path.read_text()=='preserve'


def test_failure_report_omits_private_exception_text(tmp_path,monkeypatch,capsys,caplog):
    from scripts import verify_cloud_backend as probe
    path=tmp_path/'report.json'
    async def scenario(args,report):
        report['phase']='network'
        raise OSError('ROOT-KEY and private HTTP response')
    monkeypatch.setattr(probe,'scenario',scenario)
    monkeypatch.setattr(sys,'argv',arguments(path))
    assert probe.main()==1
    result=json.loads(path.read_text())
    assert result['status']=='failed' and result['company_acceptance']=='pending'
    assert result['phase']=='network' and result['error_type']=='OSError'
    assert 'ROOT-KEY' not in path.read_text()+capsys.readouterr().out+caplog.text


def test_partial_external_relay_config_fails_before_report_or_docker(tmp_path,monkeypatch):
    from scripts import verify_cloud_backend as probe
    path=tmp_path/'report.json'
    monkeypatch.setattr(sys,'argv',arguments(path)+['--external-http-url','http://fixture/'])
    with pytest.raises(SystemExit) as stopped:probe.main()
    assert stopped.value.code==2 and not path.exists()


def test_complete_external_config_reaches_probe_with_explicit_expectation(tmp_path,monkeypatch):
    from scripts import verify_cloud_backend as probe
    path=tmp_path/'report.json';seen=[]
    async def scenario(args,report):
        seen.append((args.external_http_url,args.external_grpc_target,args.external_expect))
    monkeypatch.setattr(probe,'scenario',scenario)
    monkeypatch.setattr(sys,'argv',arguments(path)+[
        '--external-http-url','http://fixture/','--external-http-sha256','a'*64,
        '--external-grpc-target','fixture:1234','--external-expect','offline'])
    assert probe.main()==0
    assert seen==[('http://fixture/','fixture:1234','offline')]
    assert json.loads(path.read_text())['company_acceptance']=='pending'
