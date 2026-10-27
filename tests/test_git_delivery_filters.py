import os
import pytest
from git_delivery_helpers import prepared_result
from git_input_helpers import git
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.contracts.git_delivery import GitDeliveryError
from naiyue_test_agent.contracts.projects import LocalSource
from naiyue_test_agent.delivery_settings import DeliverySettings
from naiyue_test_agent.execution.git_delivery import GitDelivery


@pytest.mark.parametrize('attributes',['info','worktree'])
@pytest.mark.parametrize('entry',['plan','execute','reconcile'])
def test_host_filter_never_runs_during_delivery(tmp_path,attributes,entry):
    root,_,importer,receipt,_=prepared_result(tmp_path)
    driver=GitDelivery(importer,LocalSource(root),receipt,DeliverySettings())
    plan=driver.plan('merge')
    marker=tmp_path/'filter-executed'
    script=tmp_path/'filter.sh';script.write_text(f'#!/bin/sh\ntouch "{marker}"\ncat\n');script.chmod(0o700)
    git(root,'config','filter.fixture.clean',str(script))
    target=root/'.git'/'info'/'attributes' if attributes=='info' else root/'.gitattributes'
    target.write_text('app.txt filter=fixture\n')
    os.utime(root/'app.txt',ns=(1_000_000_000,1_000_000_000))
    try:
        if entry=='plan':driver.plan('merge')
        elif entry=='execute':driver.execute(plan)
        else:assert driver.reconcile(plan)['state']=='unknown'
    except (GitInputError,GitDeliveryError):pass
    assert not marker.exists()
    assert git(root,'rev-parse','HEAD').decode().strip()==receipt['original_head']


def test_preflight_rejects_filter_matching_only_a_future_checkout_path(tmp_path):
    root,_,importer,receipt,_=prepared_result(tmp_path)
    driver=GitDelivery(importer,LocalSource(root),receipt,DeliverySettings())
    (root/'.gitattributes').write_text('answer.txt filter=fixture\n')
    with pytest.raises(GitInputError,match='git_input_unsupported'):driver.plan('merge')
