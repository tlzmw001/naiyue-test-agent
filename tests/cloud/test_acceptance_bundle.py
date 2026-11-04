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


def test_installed_ci_python_newlines_are_normalized_but_code_changes_are_rejected(tmp_path,monkeypatch):
    import naiyue_test_agent
    from scripts.verify_cloud_candidate import verify
    installed=tmp_path/'installed';installed.mkdir()
    package=installed/'__init__.py';package.write_bytes(b'x = 1\r\n')
    monkeypatch.setattr(naiyue_test_agent,'__file__',str(package))
    bundle=tmp_path/'bundle';bundle.mkdir()
    manifest=dict(schema=1,files={},python_newlines='lf',
        installed_files={'__init__.py':hashlib.sha256(b'x = 1\n').hexdigest()})
    (bundle/'candidate.json').write_text(json.dumps(manifest))
    assert verify(bundle,installed=True)['installed_files']==1
    package.write_bytes(b'x = 2\r\n')
    with pytest.raises(ValueError,match='candidate_installed_mismatch'):verify(bundle,installed=True)


def test_builder_accepts_ci_line_endings_without_rewriting_wheel_bytes(tmp_path,monkeypatch):
    import zipfile
    import scripts.build_cloud_acceptance as builder
    from scripts.verify_cloud_candidate import verify
    root=tmp_path/'source';root.mkdir()
    package=root/'naiyue_test_agent';package.mkdir()
    (package/'__init__.py').write_bytes(b'x = 1\n')
    wheels=[]
    for target,data in [('linux',b'x = 1\n'),('windows',b'x = 1\r\n')]:
        path=tmp_path/(target+'.whl')
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('naiyue_test_agent/__init__.py',data)
            archive.writestr('naiyue_test_agent/_assets/web/index.html',b'web')
        wheels.append(path)
    kit=tmp_path/'kit.whl'
    with zipfile.ZipFile(kit,'w') as archive:
        archive.writestr('aitest_kit-0.6.0.dist-info/METADATA','Name: aitest-kit\nVersion: 0.6.0\n')
    (root/'release').mkdir()
    (root/'release/cloud-test-toolchain.json').write_text(json.dumps(dict(aitest_kit=dict(
        version='0.6.0',wheel_sha256=hashlib.sha256(kit.read_bytes()).hexdigest()))))
    for name in ('cloud-test-requirements.txt','native-tools.json'):(root/'release'/name).write_text('fixture')
    # Limit only the external archive/checkout fixture; production source comparison and copy remain real.
    monkeypatch.setattr(builder,'DOCS',())
    monkeypatch.setattr(builder,'SCRIPTS',())
    monkeypatch.setattr(builder,'inspect_release',lambda wheels,version:[dict(target='linux'),dict(target='windows')])
    monkeypatch.setattr(builder.subprocess,'check_output',lambda args,**kwargs:'a'*40 if args[1]=='rev-parse' else '')
    output=tmp_path/'bundle'
    builder.create(root,output,wheels,kit)
    assert verify(output)['status']=='passed'
    manifest=json.loads((output/'candidate.json').read_text())
    assert manifest['python_newlines']=='lf'
    for path in wheels:assert (output/'wheels'/path.name).read_bytes()==path.read_bytes()
    assert manifest['installed_files']['__init__.py']==hashlib.sha256(b'x = 1\n').hexdigest()
