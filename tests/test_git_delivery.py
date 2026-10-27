"""Branch, clean merge and discard are explicit, finite local Git mutations."""
import pytest
from git_input_helpers import git
from git_delivery_helpers import prepared_result
from naiyue_test_agent.contracts.projects import LocalSource,GitSource


def driver(tmp_path,**kwargs):
    from naiyue_test_agent.execution.git_delivery import GitDelivery
    from naiyue_test_agent.delivery_settings import DeliverySettings
    root,task,importer,receipt,first=prepared_result(tmp_path,**kwargs)
    return GitDelivery(importer,LocalSource(root),receipt,DeliverySettings()),root,task,receipt,first


def test_branch_only_creates_ref_with_dirty_partial_index(tmp_path):
    delivery,root,_,receipt,_=driver(tmp_path,dirty_input=True)
    git(root,'add','app.txt');(root/'app.txt').write_text('later user edit\n')
    before=(git(root,'rev-parse','HEAD'),(root/'.git'/'index').read_bytes(),(root/'app.txt').read_bytes())
    plan=delivery.plan('branch',branch='result/review');delivery.execute(plan)
    assert git(root,'rev-parse','refs/heads/result/review').decode().strip()==receipt['output_commit']
    assert before==(git(root,'rev-parse','HEAD'),(root/'.git'/'index').read_bytes(),(root/'app.txt').read_bytes())
    assert delivery.reconcile(plan)['state']=='completed'
    with pytest.raises(Exception,match='git_delivery_branch_exists'):delivery.plan('branch',branch='result/review')


def test_clean_linear_merge_retains_exact_task_commits_and_no_snapshot(tmp_path):
    delivery,root,_,receipt,first=driver(tmp_path)
    plan=delivery.plan('merge');assert plan['commits']==[first,receipt['output_commit']]
    progress=[];delivery.execute(plan,on_progress=progress.append)
    assert git(root,'rev-parse','HEAD').decode().strip()==receipt['output_commit']
    assert git(root,'status','--porcelain')==b''
    assert git(root,'rev-list','--count',receipt['original_head']+'..HEAD').strip()==b'2'
    assert (root/'answer.txt').read_text()=='second agent\n'
    assert progress==[first,receipt['output_commit']]
    assert delivery.reconcile(plan)['state']=='completed'


@pytest.mark.parametrize('case',['input_wip','dirty','staged','moved_head','unfinished','untracked','ignored'])
def test_merge_gate_rejects_before_any_file_or_head_change(tmp_path,case):
    delivery,root,_,receipt,_=driver(tmp_path,dirty_input=case=='input_wip')
    if case=='dirty':(root/'app.txt').write_text('user dirty\n')
    if case=='staged':(root/'app.txt').write_text('staged\n');git(root,'add','app.txt')
    if case=='moved_head':(root/'other').write_text('user');git(root,'add','.');git(root,'commit','-qm','user new head')
    if case=='unfinished':(root/'.git'/'CHERRY_PICK_HEAD').write_text(receipt['output_commit']+'\n')
    if case in ('untracked','ignored'):
        (root/'answer.txt').write_text('keep user bytes')
        if case=='ignored':(root/'.git'/'info'/'exclude').write_text('answer.txt\n')
    before=(git(root,'rev-parse','HEAD'),(root/'.git'/'index').read_bytes(),(root/'app.txt').read_bytes())
    with pytest.raises(Exception,match='git_delivery_'):delivery.plan('merge')
    assert before==(git(root,'rev-parse','HEAD'),(root/'.git'/'index').read_bytes(),(root/'app.txt').read_bytes())


def test_discard_deletes_only_app_owned_ref_and_keeps_result_objects(tmp_path):
    delivery,root,_,receipt,_=driver(tmp_path)
    branch=delivery.plan('branch',branch='review');delivery.execute(branch)
    before=(git(root,'rev-parse','HEAD'),(root/'.git'/'index').read_bytes())
    plan=delivery.plan('discard');delivery.execute(plan)
    assert git(root,'for-each-ref','refs/naiyue/tasks/')==b''
    assert git(root,'rev-parse','refs/heads/review').decode().strip()==receipt['output_commit']
    assert before==(git(root,'rev-parse','HEAD'),(root/'.git'/'index').read_bytes())
    assert delivery.reconcile(plan)['state']=='completed'


def test_interruption_keeps_partial_merge_and_reconcile_does_not_continue(tmp_path):
    delivery,root,_,receipt,first=driver(tmp_path)
    plan=delivery.plan('merge')
    def interrupt(oid):raise OSError('fixture journal save failed')
    with pytest.raises(OSError):delivery.execute(plan,on_progress=interrupt)
    assert git(root,'rev-parse','HEAD').decode().strip()==first
    assert delivery.reconcile(plan)['state']=='unknown'
    assert git(root,'rev-parse','HEAD').decode().strip()==first
