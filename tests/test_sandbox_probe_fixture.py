"""Only generated source is used by the input performance experiment."""
def test_f1_has_exactly_671_files_and_20_bounded_skills(tmp_path):
    from scripts.sandbox_probe.fixtures import populate
    source=tmp_path/'source'
    populate(source)
    files=[path for path in source.rglob('*') if path.is_file()]
    skills=[path for path in files if path.name=='SKILL.md']
    assert len(files)==671 and len(skills)==20
    assert all(path.stat().st_size<=4096 for path in skills)
    assert sum(path.stat().st_size for path in files)<8*1024*1024


def test_snapshot_fixture_canonicalizes_its_generated_temporary_root(tmp_path):
    from scripts.sandbox_probe.fixtures import snapshot_fixture
    real=tmp_path/'real';real.mkdir()
    alias=tmp_path/'alias';alias.symlink_to(real,target_is_directory=True)
    report={}
    snapshot,bundle=snapshot_fixture(alias,report)
    assert len(snapshot.manifest)==671
    assert bundle.is_file() and report['snapshot']['samples']==1


def test_snapshot_fixture_ignores_ambient_git_redirection(tmp_path,monkeypatch):
    from scripts.sandbox_probe.fixtures import snapshot_fixture
    external=tmp_path/'outside.git';work=tmp_path/'outside-work';work.mkdir()
    fixture=tmp_path/'generated';fixture.mkdir()
    monkeypatch.setenv('GIT_DIR',str(external))
    monkeypatch.setenv('GIT_WORK_TREE',str(work))
    monkeypatch.setenv('GIT_INDEX_FILE',str(tmp_path/'outside-index'))
    snapshot,_=snapshot_fixture(fixture,{})
    assert len(snapshot.manifest)==671 and not external.exists()
    assert list(work.iterdir())==[] and not (tmp_path/'outside-index').exists()
