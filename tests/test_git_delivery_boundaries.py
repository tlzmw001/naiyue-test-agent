import pytest
from git_delivery_helpers import prepared_result
from git_input_helpers import git
from naiyue_test_agent.contracts.projects import LocalSource
from naiyue_test_agent.contracts.git_delivery import GitDeliveryError
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.delivery_settings import DeliverySettings
from naiyue_test_agent.execution.git_delivery import GitDelivery
from naiyue_test_agent.execution.git_repository import GitRepository,bundle_digest


def test_delivery_never_runs_user_hooks_or_signing_commands(tmp_path):
    root,_,importer,receipt,_=prepared_result(tmp_path)
    marker=tmp_path/'executed';script=f'#!/bin/sh\ntouch "{marker}"\nexit 1\n'
    for name in ('reference-transaction','post-checkout','post-commit','pre-commit','post-rewrite'):
        path=root/'.git'/'hooks'/name;path.write_text(script);path.chmod(0o700)
    program=tmp_path/'gpg';program.write_text(script);program.chmod(0o700)
    git(root,'config','commit.gpgSign','true');git(root,'config','gpg.program',str(program))
    driver=GitDelivery(importer,LocalSource(root),receipt,DeliverySettings())
    driver.execute(driver.plan('branch',branch='review'))
    assert driver.execute(driver.plan('merge'))['state']=='completed'
    driver.execute(driver.plan('discard'))
    assert not marker.exists()


@pytest.mark.parametrize('action',['branch','discard'])
def test_symbolic_refs_are_never_treated_as_owned_mutable_refs(tmp_path,action):
    root,_,importer,receipt,_=prepared_result(tmp_path)
    before=git(root,'rev-parse','HEAD')
    ref='refs/heads/review' if action=='branch' else 'refs/naiyue/tasks/'+receipt['result_id']
    git(root,'symbolic-ref',ref,git(root,'symbolic-ref','HEAD').decode().strip())
    driver=GitDelivery(importer,LocalSource(root),receipt,DeliverySettings())
    with pytest.raises(GitDeliveryError,match='git_delivery_symbolic_ref'):
        driver.plan(action,**({'branch':'review'} if action=='branch' else {}))
    assert git(root,'rev-parse','HEAD')==before


def test_nonlinear_task_history_rejected_before_checkout(tmp_path):
    root,_,importer,receipt,first=prepared_result(tmp_path)
    tree=git(importer.repository.path,'rev-parse',receipt['output_commit']+'^{tree}').decode().strip()
    output=git(importer.repository.path,'commit-tree',tree,'-p',first,'-p',receipt['original_head'],'-m','merge task').decode().strip()
    receipt={**receipt,'output_commit':output}
    # Import only this disposable synthetic merge object, preserving the original fixture receipt identity.
    git(root,'fetch',str(importer.repository.path),output)
    git(root,'update-ref','refs/naiyue/tasks/'+receipt['result_id'],output)
    driver=GitDelivery(importer,LocalSource(root),receipt,DeliverySettings())
    with pytest.raises(GitDeliveryError,match='git_delivery_nonlinear'):driver.plan('merge')
    assert git(root,'rev-parse','HEAD').decode().strip()==receipt['original_head']


@pytest.mark.parametrize('entry',['verify','merge'])
def test_equivalent_link_escape_is_rejected_before_result_delivery(tmp_path,entry):
    root,task,importer,receipt,_=prepared_result(tmp_path)
    task_repo=GitRepository(task,importer.settings)
    for name,target in [('a',b'.'),('b',b'A/../outside.txt')]:
        oid=task_repo.run(['hash-object','-w','--stdin'],data=target).decode().strip()
        git(task,'update-index','--add','--cacheinfo','120000',oid,name)
    git(task,'commit','-qm','untrusted link aliases')
    output=git(task,'rev-parse','HEAD').decode().strip()
    ref='refs/naiyue/tasks/'+receipt['result_id']
    git(task,'update-ref',ref,output)
    bundle=tmp_path/'unsafe-result.bundle';git(task,'bundle','create',str(bundle),ref)
    size,digest=bundle_digest(bundle,importer.settings.max_bundle_bytes)
    changed={**receipt,'output_commit':output,'bundle_size':size,'bundle_sha256':digest}
    before=(git(root,'rev-parse','HEAD'),(root/'.git/index').read_bytes(),(root/'app.txt').read_bytes())
    if entry=='merge':
        # Simulate a result cached by an older validator; the checkout gate must recheck it.
        git(importer.repository.path,'fetch',str(task),ref+':'+ref)
        git(root,'fetch',str(task),ref+':'+ref)
    with pytest.raises(GitInputError,match='git_input_outside'):
        if entry=='verify':importer.verify(bundle,changed,receipt)
        else:
            driver=GitDelivery(importer,LocalSource(root),changed,DeliverySettings())
            driver.plan('merge')
    assert before==(git(root,'rev-parse','HEAD'),(root/'.git/index').read_bytes(),(root/'app.txt').read_bytes())
    assert not (root/'a').is_symlink() and not (root/'b').is_symlink()
    if entry=='verify':assert git(root,'rev-parse',ref).decode().strip()==receipt['output_commit']
