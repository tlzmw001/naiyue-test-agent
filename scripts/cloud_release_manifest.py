"""Assemble the cloud update index from standard wheels; never publish or deploy."""
import argparse,hashlib,json,shutil,zipfile
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urljoin

from scripts.release_artifacts import inspect_release
from naiyue_test_agent.contracts.cloud_releases import CloudRelease,ReleaseArtifact,ReleaseIndex
from naiyue_test_agent.contracts.cloud_versions import OWNER_PROTOCOL,RUNNER_PROTOCOL,STATE_SCHEMA



def create(*,wheel_dir,kit_wheel,kit_profile,test_lock,release_id,source_commit,base_image,public_base_url,output,previous_index=None,min_coordinator_version=None,channel='stable'):
    from scripts.release_artifacts import inspect_wheel
    wheels=sorted(Path(wheel_dir).glob('*.whl'))
    if not wheels:raise ValueError('cloud_release_wheels_missing')
    version=inspect_wheel(wheels[0])['version'];inspect_release(wheels,version)
    profile=json.loads(Path(kit_profile).read_bytes())['aitest_kit']
    if hashlib.sha256(Path(kit_wheel).read_bytes()).hexdigest()!=profile['wheel_sha256']:raise ValueError('cloud_release_kit_mismatch')
    with zipfile.ZipFile(kit_wheel) as archive:
        names=[name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if len(names)!=1:raise ValueError('cloud_release_kit_mismatch')
        metadata=BytesParser().parsebytes(archive.read(names[0]))
        if metadata['Name'].lower().replace('_','-')!='aitest-kit' or metadata['Version']!=profile['version']:
            raise ValueError('cloud_release_kit_mismatch')
    if ('aitest-kit=='+profile['version']) not in Path(test_lock).read_text().splitlines():raise ValueError('cloud_release_kit_mismatch')
    def artifact(path):
        path=Path(path)
        return ReleaseArtifact(filename=path.name,url=urljoin(public_base_url.rstrip('/')+'/',path.name),
            size=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    records=[inspect_wheel(w) for w in wheels]
    manifest=CloudRelease(release_id=release_id,agent_version=version,source_commit=source_commit,min_coordinator_version=min_coordinator_version or version,
        coordinator_protocol=OWNER_PROTOCOL,runner_protocol=RUNNER_PROTOCOL,state_schema=STATE_SCHEMA,min_connector_protocol=OWNER_PROTOCOL,
        required_capabilities=['releases:write'],wheels={r['target']:artifact(w) for r,w in zip(records,wheels)},
        kit=artifact(kit_wheel),kit_version=profile['version'],test_lock=artifact(test_lock),base_image=base_image)
    source_files=[*wheels,Path(kit_wheel),Path(test_lock)]
    if len({p.name for p in source_files})!=len(source_files):raise ValueError('cloud_release_duplicate_filename')
    output=Path(output);output.mkdir(mode=0o700,parents=True,exist_ok=False)
    expected={a.filename:a.sha256 for a in [*manifest.wheels.values(),manifest.kit,manifest.test_lock]}
    for source in source_files:
        target=output/source.name;shutil.copyfile(source,target)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=expected[source.name]:raise ValueError('cloud_release_source_changed')
    filename=output/(release_id+'.json');filename.write_text(manifest.model_dump_json(indent=2));filename.chmod(0o600)
    previous=ReleaseIndex.model_validate_json(Path(previous_index).read_bytes()).model_dump() if previous_index else dict(schema_version=1,releases={})
    entry=artifact(filename).model_dump()
    old=previous['releases'].get(release_id)
    if old is not None and old['sha256']!=entry['sha256']:raise ValueError('cloud_release_identity_conflict')
    index=ReleaseIndex.model_validate(dict(schema_version=1,releases=previous['releases']|{release_id:entry},
        channels=previous.get('channels',{})|{channel:release_id})).model_dump()
    path=output/'index.json';path.write_text(json.dumps(index,indent=2)+'\n');path.chmod(0o600)
    return dict(manifest=manifest.model_dump(),index=index)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('wheel-dir','kit-wheel','kit-profile','test-lock','output'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('release-id','source-commit','base-image','public-base-url'):p.add_argument('--'+name,required=True)
    p.add_argument('--previous-index',type=Path)
    p.add_argument('--min-coordinator-version')
    p.add_argument('--channel',default='stable')
    args=p.parse_args();result=create(**vars(args));print(json.dumps(dict(release_id=result['manifest']['release_id'],output=str(args.output))))


if __name__=='__main__':main()
