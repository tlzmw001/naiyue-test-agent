"""Native installed checks in disposable paths; no company SSH or service access."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def network_scenario(root,*,git_executable=None):
    from naiyue_test_agent.cloud_files.source_scope import tree,plan_scope,preview_scope,select_files
    from naiyue_test_agent.cloud_files.cache import capture_cached
    from naiyue_test_agent.cloud_files.archive import validate_package
    from naiyue_test_agent.cloud_files.service_tunnels import ReverseProcess
    from naiyue_test_agent.cloud_runner.state import read_json,write_json
    from naiyue_test_agent.contracts.cloud_inputs import InputLimits
    root=Path(root).resolve()/'网络 native checks';root.mkdir()
    project=root/'项目 with spaces';project.mkdir();(project/'Src').mkdir()
    (project/'Src'/'样例.txt').write_text('native file\n',encoding='utf-8')
    limits=InputLimits(git_executable=git_executable or 'git')
    listing=tree(project,limits,path='Src')
    assert [v['name'] for v in listing['entries']]==['样例.txt']
    selected,_=plan_scope(project,limits,mode='selection',paths=['Src'])
    preview=preview_scope(project,limits,mode='selection',paths=['Src'])
    assert preview['file_count']==1 and preview['issues']==[]
    captured=capture_cached(project,selected,root/'cache',limits,codec='tar')
    manifest,_=validate_package(captured.directory/'archive',captured.codec,limits,expected_id=captured.input_id)
    assert [v.path for v in manifest.entries]==['Src/样例.txt']
    attachments=root/'附件';attachments.mkdir()
    content={'隐藏 目录/.env':b'EXPLICIT_SAMPLE=1\r\n','.git/config':b'ordinary selected bytes',
        '原样 archive.zip':b'opaque archive bytes\0'}
    for name,data in content.items():
        path=attachments/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    listing=tree(attachments,limits,ordinary=True)
    assert {item['name'] for item in listing['entries']}=={'隐藏 目录','.git','原样 archive.zip'}
    selected=select_files(attachments,limits,['隐藏 目录','.git','原样 archive.zip'])
    assert selected.schema_version==3 and set(selected.paths)==set(content)
    captured=capture_cached(attachments,selected,root/'attachment-cache',limits,codec='tar')
    expanded=root/'附件展开';expanded.mkdir()
    manifest,selection=validate_package(captured.directory/'archive',captured.codec,limits,destination=expanded,
        expected_id=captured.input_id,expected_archive_sha256=captured.archive_sha256,expected_size=captured.size)
    assert manifest.schema_version==selection.schema_version==3
    assert {entry.path for entry in manifest.entries}==set(content)
    assert {name:(expanded/name).read_bytes() for name in content}==content
    output_files_scenario(root)
    state=root/'网络.json'
    write_json(state,{'revision':1,'address':'http://example.test'})
    assert read_json(state)['revision']==1
    write_json(state,{'revision':2,'address':'https://example.test'})
    assert read_json(state)['revision']==2
    git='unavailable'
    if git_executable:
        subprocess.run([git_executable,'init','-q',str(project)],check=True,timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        selected,receipt=plan_scope(project,limits,mode='git',paths=['src' if os.name=='nt' else 'Src'])
        assert selected.paths==['Src/样例.txt'] and receipt.root==project
        git='passed'
    # A real local child exercises Win32 flags, CRLF pipe reads and terminate/wait.
    # Its synthetic ACK is deliberately not a claim of real SSH connectivity.
    code="import sys,time;sys.stderr.write('debug1: remote forward success for: listen 172.18.0.1:21001, connect example.test:443\\r\\n');sys.stderr.flush();time.sleep(60)"
    child=ReverseProcess([sys.executable,'-c',code,'-R','172.18.0.1:21001:example.test:443','local-smoke'],5)
    try:
        assert child.acknowledged.wait(10) and child.ready
    finally:child.close()
    assert not child.alive and not child.reader.is_alive()
    return dict(source_tree='passed',capture_archive='passed',state_replace='passed',
        git_scope=git,child_process='passed',real_ssh='not_tested',
        attachment_roundtrip='passed',output_files_save='passed')


def output_files_scenario(root):
    """Real native cache/save IO; only the metadata repository is an in-memory fixture."""
    import hashlib
    from types import SimpleNamespace
    from uuid import uuid4
    from naiyue_test_agent.application.cloud_local.deliveries import LocalDeliveries
    from naiyue_test_agent.application.cloud_local.delivery_files import deliver_files
    from naiyue_test_agent.application.cloud_local.receive_files import ReceiveFiles
    from naiyue_test_agent.application.projects.locations import directory_facts,directory_identity
    from naiyue_test_agent.contracts.cloud_artifacts import ArtifactRecord
    from naiyue_test_agent.contracts.cloud_delivery import DeliveryRecord,DeliveryRequest
    from naiyue_test_agent.contracts.cloud_receive import ReceiveRecord,ReceiveFile,ReceiveLimits,MaterialRef
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    uid=lambda:str(uuid4())
    pid,sid,rid,mid=uid(),uuid4().hex,uid(),uid()
    data=b'received output\r\n';name='报告 目录/.env'
    artifact=ArtifactRecord(command_id=uid(),artifact_id=uid(),relative_name=name,task_id=uuid4().hex,
        project_id=uid(),input_id=None,generation=1,captured_at=1.0,size=len(data),sha256=hashlib.sha256(data).hexdigest())
    receive=ReceiveRecord(receive_id=rid,command_id=uid(),local_project_id=pid,local_session_id=sid,binding_id=uid(),
        source_handle=None,instance_id=uid(),task_id=artifact.task_id,result_id=uid(),input_id=None,phase='available',
        files=[ReceiveFile(material_id=mid,role='artifact',artifact=artifact,offset=len(data),verified=True)])
    files=ReceiveFiles(SimpleNamespace(path=root/'materials.sqlite'))
    stage=files.stage(rid,artifact.artifact_id);stage.write_bytes(data);stage.chmod(0o600)
    identity=files.publish(stage,artifact)
    material=MaterialRef(material_id=mid,receive_id=rid,task_id=artifact.task_id,result_id=receive.result_id,input_id=None,
        title=name,role='artifact',size=len(data),sha256=artifact.sha256,source_run_id=uuid4().hex,outcome='completed',captured_at=1.0)
    def lookup(material_id,session_id,project_id):
        assert (material_id,session_id,project_id)==(mid,sid,pid)
        return material,identity
    class Records:
        def read(self,cid,session_id):
            assert (cid,session_id)==(self.record.command_id,sid)
            return self.record,self.meta
        def save(self,record,meta):self.record,self.meta=record,meta
        def complete(self,record,meta):self.save(record,meta)
    records=Records()
    domain=SimpleNamespace(project_id=pid,authority=object(),deliveries=records,
        materials=SimpleNamespace(files=files,repo=SimpleNamespace(material=lookup)))
    manager=LocalDeliveries(SimpleNamespace(settings=SimpleNamespace(receive_limits=ReceiveLimits())))
    destination=root/'保存结果';destination.mkdir();(destination/'报告 目录').mkdir()
    (destination/name).write_bytes(b'existing local content')
    for index,folder in enumerate(('','','新建文件夹')):
        cid=uid()
        request=DeliveryRequest(command_id=cid,local_session_id=sid,receive_id=rid,action='files',destination_handle=pid,
            destination_path=str(destination),expected_directory=directory_identity(destination,directory_facts(destination)),name=folder)
        _,signature=manager.root(domain,request)
        record=DeliveryRecord(command_id=cid,local_project_id=pid,local_session_id=sid,receive_id=rid,action='files',name=folder,phase='preparing')
        records.save(record,dict(request=request.model_dump(mode='json'),root_identity=signature))
        work=files.directory('deliveries',cid)
        deliver_files(manager,domain,record,receive,work,False)
        assert records.record.phase=='prepared' and bool(records.record.conflicts)==(index==0)
        if records.record.conflicts:
            assert records.record.conflicts==[name] and records.record.force_allowed
            try:deliver_files(manager,domain,records.record,receive,work,True)
            except CloudError as error:assert error.code=='cloud_delivery_conflict'
            else:raise AssertionError('Different local content was overwritten without confirmation')
            assert (destination/name).read_bytes()==b'existing local content'
            records.meta['force']=True
        deliver_files(manager,domain,records.record,receive,work,True)
        assert records.record.phase=='completed' and records.record.done==[name]
        assert (destination/folder/name).read_bytes()==data
    assert receive.input_package is None and receive.input_id is None


def verify_network_report(report):
    if not isinstance(report,dict) or any(report.get(k)!='passed' for k in
            ('source_tree','capture_archive','state_replace','child_process','attachment_roundtrip','output_files_save')):
        raise ValueError('installed_network_checks_failed')
    if report.get('git_scope') not in ('passed','unavailable'):raise ValueError('installed_git_check_invalid')


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='nyt-network-smoke-') as path:
        result=network_scenario(Path(path),git_executable=shutil.which('git'))
        verify_network_report(result);print(json.dumps(result,ensure_ascii=False))
