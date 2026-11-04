"""Assemble a local same-source acceptance kit; no upload, deploy or publication."""
import argparse,hashlib,json,shutil,subprocess,zipfile
from pathlib import Path
from scripts.release_artifacts import inspect_release,release_source
from scripts.verify_cloud_candidate import sha,verify,python_sha

DOCS=('cloud-final-windows-acceptance.md','cloud-acceptance-report-template.md','cloud-updates.md','cloud-result-delivery.md','cloud-startup-and-configuration.md',
    'cloud-s091-windows-and-linux.md','cloud-s00-local-and-windows.md','cloud-s00-ai-handoff.md',
    'specs/143g-acceptance.md','specs/143e-a-to-b-cleanup.md','specs/143ah-s102-operations.md','specs/143ai-s11-final-acceptance.md')
DOCS+=tuple('examples/full/'+name for name in ('settings.full.toml','cloud-configuration-reference.md',
    'template-build.example.json','profiles.example.json','connection.example.json','cloud.example.json','update.example.json','remote-update.example.json'))
SCRIPTS=('verify_cloud_candidate.py','verify_cloud_workflow.py','verify_cloud_backend.py',
    'profile_result_capture.py','cloud_release_manifest.py','release_artifacts.py','native_assets.py')


def create(root,output,wheels,kit):
    root=Path(root).resolve();output=Path(output).absolute();wheels=[Path(p).absolute() for p in wheels];kit=Path(kit).absolute()
    import zipfile
    from email.parser import BytesParser
    from naiyue_test_agent import __version__
    records=inspect_release(wheels,__version__)
    profile=json.loads((root/'release/cloud-test-toolchain.json').read_text())['aitest_kit']
    if sha(kit)!=profile['wheel_sha256']:raise ValueError('candidate_kit_mismatch')
    with zipfile.ZipFile(kit) as archive:
        metadata=[n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
        if len(metadata)!=1:raise ValueError('candidate_kit_mismatch')
        value=BytesParser().parsebytes(archive.read(metadata[0]))
        if (value['Name'].lower().replace('_','-'),value['Version'])!=('aitest-kit',profile['version']):raise ValueError('candidate_kit_mismatch')
    production={p.relative_to(root/'naiyue_test_agent').as_posix():python_sha(release_source(p,root).read_bytes()) for p in (root/'naiyue_test_agent').rglob('*.py')}
    installed=dict(production)
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as z:
            actual={n.removeprefix('naiyue_test_agent/'):python_sha(z.read(n)) for n in z.namelist()
                if n.startswith('naiyue_test_agent/') and n.endswith('.py')}
            if actual!=production:raise ValueError('candidate_source_mismatch')
            web={n.removeprefix('naiyue_test_agent/'):hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith('naiyue_test_agent/_assets/web/')}
            if len(installed)>len(production) and any(installed.get(n)!=s for n,s in web.items()):raise ValueError('candidate_web_mismatch')
            installed.update(web)
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    def copy(source,name):
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    for wheel in wheels:copy(release_source(wheel,wheel.parent),'wheels/'+wheel.name)
    copy(release_source(kit,kit.parent),'wheels/'+kit.name)
    for name in DOCS:copy(release_source(root/'docs'/name,root),'docs/'+name)
    for name in SCRIPTS:copy(release_source(root/'scripts'/name,root),'scripts/'+name)
    for sub in ('cloud_workflow','cloud_probe'):
        for path in sorted((root/'scripts'/sub).glob('*.py')):copy(release_source(path,root),'scripts/'+sub+'/'+path.name)
    for name in ('cloud-test-toolchain.json','cloud-test-requirements.txt','native-tools.json'):
        copy(release_source(root/'release'/name,root),'release/'+name)
    files={p.relative_to(output).as_posix():dict(sha256=sha(p),size=p.stat().st_size) for p in sorted(output.rglob('*')) if p.is_file()}
    # The candidate identity covers executable source and every delivered byte,
    # while HEAD is explicitly only the base for this uncommitted candidate.
    basis=dict(installed_files=installed,files=files)
    fingerprint=hashlib.sha256(json.dumps(basis,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    manifest=dict(schema=1,python_newlines='lf',candidate_id='s11-'+fingerprint[:16],source_base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
        source_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=root)),source_sha256=fingerprint,version=__version__,
        files=files,installed_files=installed,targets=[r['target'] for r in records])
    (output/'candidate.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    entries=files|{'candidate.json':dict(sha256=sha(output/'candidate.json'))}
    (output/'SHA256SUMS').write_text(''.join(f'{value["sha256"]}  {name}\n' for name,value in sorted(entries.items())),encoding='utf-8')
    verify(output)
    archive=output.with_suffix('.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(output.rglob('*')):
            if path.is_file():z.write(path,path.relative_to(output))
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:raise ValueError('candidate_zip_corrupt')
        if set(z.namelist())!={p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}:raise ValueError('candidate_zip_members')
    archive.with_suffix('.zip.sha256').write_text(sha(archive)+'  '+archive.name+'\n')
    return dict(candidate_id=manifest['candidate_id'],zip=str(archive),sha256=sha(archive),bytes=archive.stat().st_size)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--wheel',type=Path,action='append',required=True)
    parser.add_argument('--kit',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(create(args.root,args.output,args.wheel,args.kit),indent=2))


if __name__=='__main__':main()
