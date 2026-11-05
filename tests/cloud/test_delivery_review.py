"""Preflight observes only changed paths; force uses the observed local bytes."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from test_delivery_apply import output as fixture_output,entry
from naiyue_test_agent.cloud_files.delivery_verify import VerifiedOutput

def output(tmp_path):
    root,value=fixture_output(tmp_path)
    return root,VerifiedOutput(value.directory,None,value.output_manifest,value.changes,None)


def test_review_force_preserves_real_local_backup_and_unrelated_files(tmp_path):
    from naiyue_test_agent.cloud_files.delivery_review import review,approved
    from naiyue_test_agent.cloud_files.delivery_apply import apply_changes
    root,verified=output(tmp_path);(root/'a').write_bytes(b'local work');(root/'b').write_bytes(b'local addition')
    observed,conflicts=review(root,verified,1024)
    assert conflicts==['a','b']
    with pytest.raises(CloudError,match='cloud_delivery_conflict'):approved(root,verified,observed,False)
    actual=approved(root,verified,observed,True)
    apply_changes(root,actual,tmp_path/'journal',lambda v:None)
    assert (tmp_path/'journal/0').read_bytes()==b'local work'
    assert (tmp_path/'journal/1').read_bytes()==b'local addition'
    assert (root/'unrelated').read_text()=='dirty'
    assert (root/'a').read_bytes()==b'new\r\n'


def test_force_does_not_authorize_a_later_edit_or_unsafe_parent(tmp_path):
    from naiyue_test_agent.cloud_files.delivery_review import review,approved
    root,verified=output(tmp_path);observed,_=review(root,verified,1024)
    (root/'a').write_text('later edit')
    with pytest.raises(CloudError,match='cloud_delivery_preflight_changed'):approved(root,verified,observed,True)
    outside=tmp_path/'outside';outside.mkdir();(root/'linked').symlink_to(outside,target_is_directory=True)
    verified=replace(verified,changes=[dict(path='linked/file',old=None,new=entry('linked/file',b'x'))])
    with pytest.raises((CloudError,OSError)):review(root,verified,1024)
    assert list(outside.iterdir())==[]


def test_rebase_includes_reversal_to_input_and_deletion_of_previous_addition(tmp_path):
    from naiyue_test_agent.cloud_files.delivery_review import rebased
    root,verified=output(tmp_path)
    previous=[entry('a',b'round1'),entry('removed',b'created last time')]
    next_output=rebased(verified,[e.model_dump() for e in previous])
    bypath={c['path']:c for c in next_output.changes}
    assert set(bypath)=={'a','b','removed'}
    assert bypath['a']['old']==previous[0] and bypath['removed']['new'] is None


def test_destination_scope_recognizes_an_identity_alias_with_different_spelling(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_files import delivery_review
    root=tmp_path/'real';root.mkdir();alias=tmp_path/'alias';alias.mkdir()
    from naiyue_test_agent.cloud_files.delivery_paths import directory_identity
    signature=directory_identity(root)
    monkeypatch.setattr(delivery_review,'directory_identity',lambda p:signature if p==alias else directory_identity(p))
    a=delivery_review.directory_scope(root);b=delivery_review.directory_scope(alias)
    assert a[0]==b[0] and a[0] in b
    assert not root.is_relative_to(alias) and not alias.is_relative_to(root)


def test_approval_uses_the_same_platform_mode_equivalence_as_preflight(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_files import delivery_review
    root,verified=output(tmp_path)
    changes=[dict(c) for c in verified.changes]
    changes[0]['old']=changes[0]['old'].model_copy(update={'mode':0o755})
    verified=replace(verified,changes=changes)
    original=delivery_review.matches
    def platform_matches(root,path,value,*,ordinary=False):
        return original(root,path,value.model_copy(update={'mode':0o644}) if value else None,ordinary=ordinary)
    monkeypatch.setattr(delivery_review,'matches',platform_matches)
    observed,conflicts=delivery_review.review(root,verified,1024)
    assert conflicts==[]
    approved=delivery_review.approved(root,verified,observed,False)
    assert approved.changes[0]['old'].mode==0o644
