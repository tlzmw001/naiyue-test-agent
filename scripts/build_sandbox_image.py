"""Build a sandbox image from the execution-only Linux wheel, never a checkout mount."""
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from scripts.envd_artifacts import inspect_envd_wheel


def load_image_profile():
    profile = json.loads((Path(__file__).resolve().parents[1] / 'release/sandbox-image.json').read_text())
    kit = profile.get('aitest_kit', {})
    if (set(profile) != {'schema_version', 'aitest_kit'} or profile['schema_version'] != 1 or
            set(kit) != {'version', 'wheel_sha256'} or
            not isinstance(kit['version'], str) or re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', kit['version']) is None or
            not isinstance(kit['wheel_sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', kit['wheel_sha256']) is None):
        raise ValueError('Invalid sandbox image profile')
    return profile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel-dir',type=Path,required=True)
    parser.add_argument('--base-image',required=True)
    parser.add_argument('--tag',required=True)
    parser.add_argument('--context',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    wheels=list(args.wheel_dir.glob('*.whl'))
    if len(wheels)!=1:raise ValueError('Exactly one execution Linux wheel is required')
    wheel=wheels[0].resolve();info=inspect_envd_wheel(wheel)
    if not info['target'].startswith('linux-'):raise ValueError('Sandbox image requires a Linux release wheel')
    kit = load_image_profile()['aitest_kit']
    docker=['docker','--context',args.context]
    daemon=json.loads(subprocess.check_output([*docker,'info','--format','{{json .}}']))
    architecture={'aarch64':'arm64','arm64':'arm64','x86_64':'x86_64','amd64':'x86_64'}[daemon['Architecture']]
    if info['target']!='linux-'+architecture:raise ValueError('Wheel architecture does not match Docker daemon')
    with tempfile.TemporaryDirectory(prefix='nyt-sandbox-build-') as directory:
        stage=Path(directory)
        kit_name = f"aitest_kit-{kit['version']}-py3-none-any.whl"
        subprocess.run([sys.executable, '-m', 'pip', 'download', '--no-deps', '--only-binary=:all:',
            '--dest', str(stage), f"aitest-kit=={kit['version']}"], check=True)
        kit_wheel = stage / kit_name
        if not kit_wheel.is_file() or hashlib.sha256(kit_wheel.read_bytes()).hexdigest() != kit['wheel_sha256']:
            raise ValueError('AITest Kit wheel SHA-256 mismatch')
        kit_wheel.rename(stage / 'aitest.whl')
        subprocess.run([*docker,'pull',args.base_image],check=True)
        base=json.loads(subprocess.check_output([*docker,'image','inspect',args.base_image]))[0]
        shutil.copyfile(Path(__file__).resolve().parents[1]/'sandbox/Dockerfile',stage/'Dockerfile')
        shutil.copyfile(wheel,stage/'envd.whl')
        with zipfile.ZipFile(wheel) as archive:
            metadata=BytesParser().parsebytes(archive.read(next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))))
        requirements=[item for item in metadata.get_all('Requires-Dist',[]) if 'extra ==' not in item]
        (stage/'runtime.txt').write_text('\n'.join(requirements)+'\n')
        reference=base.get('RepoDigests',[base['Id']])[0]
        subprocess.run([*docker,'build','--build-arg','BASE_IMAGE='+reference,
            '--build-arg','WHEEL_NAME='+wheel.name,'--build-arg','AITEST_WHEEL_NAME='+kit_name,
            '--tag',args.tag,str(stage)],check=True)
    image=json.loads(subprocess.check_output([*docker,'image','inspect',args.tag]))[0]
    report={'wheel':info,'base_image_id':base['Id'],'base_repo_digests':base.get('RepoDigests',[]),
            'image_id':image['Id'],'context':args.context,'tag':args.tag,'daemon_id':daemon['ID'],
            'aitest_kit':kit}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
