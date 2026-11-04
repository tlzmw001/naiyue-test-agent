"""Candidate verification covers payload bytes and refuses traversal."""
import hashlib,json
import pytest


def test_candidate_verifier_rejects_changed_or_unlisted_files(tmp_path):
    from scripts.verify_cloud_candidate import verify
    data=b'candidate';(tmp_path/'file.txt').write_bytes(data)
    manifest={'schema':1,'files':{'file.txt':{'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}},'installed_files':{}}
    (tmp_path/'candidate.json').write_text(json.dumps(manifest))
    assert verify(tmp_path)['files']==1
    (tmp_path/'file.txt').write_bytes(b'altered')
    with pytest.raises(ValueError,match='candidate_file_mismatch'):verify(tmp_path)
    (tmp_path/'file.txt').write_bytes(data);(tmp_path/'extra').write_text('unlisted')
    with pytest.raises(ValueError,match='candidate_unlisted_file'):verify(tmp_path)


def test_candidate_manifest_cannot_read_outside_root(tmp_path):
    from scripts.verify_cloud_candidate import verify
    (tmp_path/'candidate.json').write_text(json.dumps(dict(schema=1,files={'../private':{}},installed_files={})))
    with pytest.raises(ValueError,match='candidate_path_invalid'):verify(tmp_path)
