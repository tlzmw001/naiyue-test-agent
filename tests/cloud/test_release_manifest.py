"""Release index generation consumes the same inspected four-wheel release set."""
import hashlib,json,zipfile
from pathlib import Path
import pytest
from test_release_artifacts import _wheel
from scripts.native_assets import load_config
from scripts.cloud_release_manifest import create


def artifacts(tmp_path):
    seed=_wheel(tmp_path);wheels=tmp_path/'wheels';wheels.mkdir()
    with zipfile.ZipFile(seed) as z:original={n:z.read(n) for n in z.namelist()}
    for target,config in load_config()['targets'].items():
        tag=config.get('wheel_tag') or 'macosx_11_0_arm64';data=dict(original)
        tools={}
        for tool in ('rg','fd'):
            content=data.pop('naiyue_test_agent/_assets/bin/'+tool+'.exe')
            name=tool+('.exe' if target.startswith('windows') else '')
            data['naiyue_test_agent/_assets/bin/'+name]=content
            tools[tool]=dict(filename=name,sha256=hashlib.sha256(content).hexdigest())
        data['naiyue_test_agent/_assets/native.json']=json.dumps(dict(target=target,wheel_tag=tag,tools=tools)).encode()
        data['naiyue_test_agent-0.1.0.dist-info/WHEEL']=f'Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: py3-none-{tag}\n'.encode()
        with zipfile.ZipFile(wheels/f'naiyue_test_agent-0.1.0-py3-none-{tag}.whl','w') as z:
            for name,content in data.items():z.writestr(name,content)
    kit=tmp_path/'aitest_kit-0.6.0-py3-none-any.whl'
    with zipfile.ZipFile(kit,'w') as z:z.writestr('aitest_kit-0.6.0.dist-info/METADATA','Name: aitest-kit\nVersion: 0.6.0\n')
    profile=tmp_path/'toolchain.json';profile.write_text(json.dumps(dict(schema_version=1,aitest_kit=dict(version='0.6.0',wheel_sha256=hashlib.sha256(kit.read_bytes()).hexdigest()))))
    lock=tmp_path/'test.lock';lock.write_text('aitest-kit==0.6.0\npytest==9.1.1\n')
    return dict(wheel_dir=wheels,kit_wheel=kit,kit_profile=profile,test_lock=lock,release_id='r2',source_commit='a'*40,
        base_image='python@sha256:'+'b'*64,public_base_url='https://releases.invalid/r2/',output=tmp_path/'bundle')


def test_generated_index_hashes_exact_manifest_and_artifacts(tmp_path):
    args=artifacts(tmp_path);value=create(**args);out=args['output']
    entry=value['index']['releases']['r2']
    assert entry['sha256']==hashlib.sha256((out/'r2.json').read_bytes()).hexdigest()
    for artifact in [*value['manifest']['wheels'].values(),value['manifest']['kit'],value['manifest']['test_lock']]:
        assert artifact['sha256']==hashlib.sha256((out/artifact['filename']).read_bytes()).hexdigest()
    assert len(value['manifest']['wheels'])==4
    with pytest.raises(FileExistsError):create(**args)


def test_generator_refuses_false_kit_version_before_creating_bundle(tmp_path):
    args=artifacts(tmp_path);profile=json.loads(args['kit_profile'].read_text());profile['aitest_kit']['version']='0.7.0';args['kit_profile'].write_text(json.dumps(profile))
    with pytest.raises(ValueError,match='cloud_release_kit_mismatch'):create(**args)
    assert not args['output'].exists()


def test_generator_explicitly_advances_the_selected_channel(tmp_path):
    args=artifacts(tmp_path);value=create(**args,channel='stable')
    assert value['index']['channels']=={'stable':'r2'}
