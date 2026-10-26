"""Real, privately verified linear task history for delivery boundary tests."""
from dataclasses import asdict
from pathlib import Path
from git_input_helpers import git,repository
from naiyue_test_agent.contracts.projects import LocalSource
from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder
from naiyue_test_agent.execution.git_repository import GitRepository,bundle_digest
from naiyue_test_agent.execution.git_results import ResultImporter
from naiyue_test_agent.sandbox_settings import GitInputSettings


def prepared_result(tmp_path,*,dirty_input=False):
    root=repository(tmp_path);policy=GitInputSettings()
    if dirty_input:(root/'app.txt').write_text('user input\n')
    snapshot=SnapshotBuilder(tmp_path/'input',policy).prepare(LocalSource(root),'a'*32)
    task=tmp_path/'task';git(tmp_path,'clone','-q',str(tmp_path/'input'/'objects.git'),str(task))
    git(task,'checkout','-q','-b','task',snapshot.input_snapshot)
    (task/'app.txt').write_text('first agent\n');git(task,'commit','-qam','first task')
    first=git(task,'rev-parse','HEAD').decode().strip()
    (task/'answer.txt').write_text('second agent\n');git(task,'add','answer.txt');git(task,'commit','-qm','second task')
    output=git(task,'rev-parse','HEAD').decode().strip();ref='refs/naiyue/tasks/'+'b'*32
    git(task,'update-ref',ref,output);bundle=tmp_path/'result.bundle';git(task,'bundle','create',str(bundle),ref)
    size,digest=bundle_digest(bundle,policy.max_bundle_bytes)
    receipt=dict(result_id='b'*32,binding_id='B',project_id='P',session_id='S',input_id='a'*32,
        original_head=snapshot.original_head,input_snapshot=snapshot.input_snapshot,output_commit=output,
        bundle_size=size,bundle_sha256=digest,excluded=[])
    importer=ResultImporter(tmp_path/'received',policy)
    importer.verify(bundle,receipt,dict(binding_id='B',project_id='P',session_id='S',**asdict(snapshot)))
    importer.publish(receipt,LocalSource(root))
    return root,task,importer,receipt,first
