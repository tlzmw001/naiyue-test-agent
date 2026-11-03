"""Untrusted raw patches become usable output only after complete I/O closure."""
import hashlib,json
from pathlib import Path
import pytest
from naiyue_test_agent.contracts.cloud_inputs import InputPackage
from naiyue_test_agent.contracts.cloud_results import OutputManifest,ResultLimits
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result
from test_result_snapshot import fixture


def result_materials(tmp_path,*,link=False):
    root,inp,plan,boundary,limits=fixture(tmp_path)
    if link:(root/'link').symlink_to('new.sh')
    spool=freeze_result(root,inp,tmp_path/'spool',plan,boundary,1,ResultLimits(),limits)
    result=json.loads((spool/'result.json').read_bytes());roles={f['role']:spool/'files'/f['artifact']['artifact_id'] for f in result['bundle']['files']}
    package=InputPackage.model_validate_json((inp/'input.json').read_bytes())
    return root,inp,package,roles,limits


def test_verified_output_restores_binary_crlf_modes_links_and_changes(tmp_path):
    from naiyue_test_agent.cloud_files.delivery_verify import verify_delivery
    root,inp,package,roles,limits=result_materials(tmp_path,link=True)
    value=verify_delivery(package,inp/'archive',roles['manifest'].read_bytes(),roles['patch'],tmp_path/'verify',limits,ResultLimits())
    assert value.input_manifest==package.manifest and value.selection==package.selection
    assert value.output_manifest.input_id==package.input_id
    assert (value.directory/'a.txt').read_bytes()==b'new\r\n'
    assert (value.directory/'old.bin').read_bytes()==(root/'old.bin').read_bytes()
    assert (value.directory/'new.sh').stat().st_mode&0o777==0o755
    assert (value.directory/'link').is_symlink() and (value.directory/'link').readlink()==Path('new.sh')
    assert not (value.directory/'remove.txt').exists()
    changes={v['path']:v for v in value.changes}
    assert changes['remove.txt']['new'] is None and changes['new.sh']['old'] is None
    assert set(changes)=={'a.txt','old.bin','remove.txt','new.sh','link'}
    assert not (root/'.git').exists()


@pytest.mark.parametrize('bad',['patch','hash','identity','extra_touched','budget','link'])
def test_incomplete_or_inconsistent_output_never_verifies(tmp_path,bad):
    from naiyue_test_agent.cloud_files.delivery_verify import verify_delivery
    root,inp,package,roles,limits=result_materials(tmp_path)
    manifest=OutputManifest.model_validate_json(roles['manifest'].read_bytes());result_limits=ResultLimits()
    if bad=='patch':roles['patch'].write_bytes(b'not a patch')
    elif bad=='hash':manifest=manifest.model_copy(update={'entries':[e.model_copy(update={'sha256':'0'*64}) if e.path=='a.txt' else e for e in manifest.entries]})
    elif bad=='identity':manifest=manifest.model_copy(update={'input_id':'0'*64})
    elif bad=='extra_touched':
        # Valid patch which also removes and re-adds an unchanged input path.
        old=package.manifest.entries[0]
        with roles['patch'].open('ab') as stream:
            stream.write(b'diff --git a/noop b/noop\nnew file mode 100644\nindex 0000000..e69de29\n')
            stream.write(b'diff --git a/noop b/noop\ndeleted file mode 100644\nindex e69de29..0000000\n')
    elif bad=='budget':result_limits=ResultLimits(max_patch_bytes=1)
    else:
        from naiyue_test_agent.contracts.cloud_results import OutputEntry
        body=b'../../outside';entry=OutputEntry(path='escape',type='symlink',size=len(body),sha256=hashlib.sha256(body).hexdigest(),mode=0o777,target=body.decode())
        manifest=manifest.model_copy(update={'entries':sorted([*manifest.entries,entry],key=lambda e:e.path)})
    with pytest.raises(CloudError):verify_delivery(package,inp/'archive',manifest.canonical,roles['patch'],tmp_path/'verify',limits,result_limits)
    assert (root/'a.txt').read_bytes()==b'new\r\n'


def test_empty_patch_is_valid_only_for_unchanged_complete_output(tmp_path):
    from naiyue_test_agent.cloud_files.delivery_verify import verify_delivery
    root,inp,package,roles,limits=result_materials(tmp_path)
    roles['patch'].write_bytes(b'')
    output=OutputManifest(input_id=package.input_id,policy_sha256=package.manifest.selection['policy_sha256'],entries=[e.model_dump() for e in package.manifest.entries])
    value=verify_delivery(package,inp/'archive',output.canonical,roles['patch'],tmp_path/'verify',limits,ResultLimits())
    assert value.changes==[] and (value.directory/'remove.txt').read_bytes()==b'remove'


def test_binary_expansion_budget_is_checked_before_git_apply(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_files.delivery_verify import verify_delivery
    from naiyue_test_agent.cloud_files.delivery_git import DeliveryGit
    root,inp,plan,boundary,limits=fixture(tmp_path);(root/'old.bin').write_bytes(b'\0'*(256*1024))
    spool=freeze_result(root,inp,tmp_path/'spool',plan,boundary,1,ResultLimits(),limits)
    result=json.loads((spool/'result.json').read_bytes());roles={f['role']:spool/'files'/f['artifact']['artifact_id'] for f in result['bundle']['files']}
    package=InputPackage.model_validate_json((inp/'input.json').read_bytes());output=OutputManifest.model_validate_json(roles['manifest'].read_bytes())
    output=output.model_copy(update={'entries':[e.model_copy(update={'size':512}) if e.path=='old.bin' else e for e in output.entries]})
    calls=[];original=DeliveryGit.run
    def watch(self,args,**kwargs):
        if 'apply' in args and '--cached' in args:calls.append(args)
        return original(self,args,**kwargs)
    monkeypatch.setattr(DeliveryGit,'run',watch)
    with pytest.raises(CloudError):
        verify_delivery(package,inp/'archive',output.canonical,roles['patch'],tmp_path/'verify',limits.model_copy(update={'max_file_bytes':1024}),ResultLimits())
    assert not calls,'binary expansion was performed before checking the configured file budget'


@pytest.mark.parametrize('fault',['target','source','declared','blocks','total','zlib'])
def test_delta_and_literal_headers_have_bounded_streaming_preflight(tmp_path,fault):
    import base64,zlib
    from naiyue_test_agent.cloud_files.delivery_patch_budget import check_patch_budget
    from naiyue_test_agent.contracts.cloud_inputs import InputLimits
    def varint(value):
        raw=bytearray()
        while value>=128:raw.append((value&127)|128);value>>=7
        return bytes([*raw,value])
    body=varint(2048 if fault=='source' else 512)+varint(2048 if fault=='target' else 512)
    declared=len(body);encoded=zlib.compress(body)
    if fault=='declared':declared=1024*1024
    if fault=='zlib':encoded=encoded[:-2]
    lines=[]
    for offset in range(0,len(encoded),52):
        chunk=encoded[offset:offset+52];size=len(chunk);prefix=bytes([64+size if size<=26 else 70+size])
        lines.append(prefix+base64.b85encode(chunk,pad=True)+b'\n')
    block=b'GIT binary patch\ndelta '+str(declared).encode()+b'\n'+b''.join(lines)+b'\n'
    patch=tmp_path/'patch';patch.write_bytes(block*(5 if fault=='blocks' else 3 if fault=='total' else 1))
    input_limits=InputLimits(max_file_bytes=1024,max_files=1,max_input_bytes=512 if fault=='total' else 128*1024*1024)
    result_limits=ResultLimits(max_files=1,max_result_bytes=512 if fault=='total' else 256*1024*1024)
    with pytest.raises(CloudError):check_patch_budget(patch,input_limits,result_limits)


@pytest.mark.parametrize('kind',['delta','compressed_literal'])
def test_legitimate_bounded_binary_blocks_still_reconstruct_exact_output(tmp_path,kind):
    from naiyue_test_agent.cloud_files.delivery_verify import verify_delivery
    root,inp,plan,boundary,limits=fixture(tmp_path)
    if kind=='delta':
        body=bytearray(bytes(range(256))*2);body[100]=42;body=bytes(body)
    else:body=b'\0'*(256*1024)
    (root/'old.bin').write_bytes(body)
    spool=freeze_result(root,inp,tmp_path/'spool',plan,boundary,1,ResultLimits(),limits)
    result=json.loads((spool/'result.json').read_bytes());roles={f['role']:spool/'files'/f['artifact']['artifact_id'] for f in result['bundle']['files']}
    package=InputPackage.model_validate_json((inp/'input.json').read_bytes())
    if kind=='delta':assert b'\ndelta ' in roles['patch'].read_bytes()
    verified=verify_delivery(package,inp/'archive',roles['manifest'].read_bytes(),roles['patch'],tmp_path/'verify',limits,ResultLimits())
    assert (verified.directory/'old.bin').read_bytes()==body
