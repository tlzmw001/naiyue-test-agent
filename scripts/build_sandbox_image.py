"""Build a sandbox image from the ordinary Linux release wheel, never a checkout mount."""
import argparse
from email.parser import BytesParser
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile
from scripts.release_artifacts import inspect_wheel


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel-dir',type=Path,required=True)
    parser.add_argument('--base-image',required=True)
    parser.add_argument('--tag',required=True)
    parser.add_argument('--context',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    wheels=list(args.wheel_dir.glob('*.whl'))
    if len(wheels)!=1:raise ValueError('Exactly one standard Linux wheel is required')
    wheel=wheels[0].resolve();info=inspect_wheel(wheel)
    if not info['target'].startswith('linux-'):raise ValueError('Sandbox image requires a Linux release wheel')
    docker=['docker','--context',args.context]
    daemon=json.loads(subprocess.check_output([*docker,'info','--format','{{json .}}']))
    architecture={'aarch64':'arm64','arm64':'arm64','x86_64':'x64','amd64':'x64'}[daemon['Architecture']]
    if info['target']!='linux-'+architecture:raise ValueError('Wheel architecture does not match Docker daemon')
    subprocess.run([*docker,'pull',args.base_image],check=True)
    base=json.loads(subprocess.check_output([*docker,'image','inspect',args.base_image]))[0]
    with tempfile.TemporaryDirectory(prefix='nyt-sandbox-build-') as directory:
        stage=Path(directory)
        shutil.copyfile(Path(__file__).resolve().parents[1]/'sandbox/Dockerfile',stage/'Dockerfile')
        shutil.copyfile(wheel,stage/'application.whl')
        with zipfile.ZipFile(wheel) as archive:
            metadata=BytesParser().parsebytes(archive.read(next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))))
        requirements=[item for item in metadata.get_all('Requires-Dist',[]) if 'extra ==' not in item]
        (stage/'runtime.txt').write_text('\n'.join(requirements)+'\n')
        reference=base.get('RepoDigests',[base['Id']])[0]
        subprocess.run([*docker,'build','--build-arg','BASE_IMAGE='+reference,
            '--build-arg','WHEEL_NAME='+wheel.name,'--tag',args.tag,str(stage)],check=True)
    image=json.loads(subprocess.check_output([*docker,'image','inspect',args.tag]))[0]
    report={'wheel':info,'base_image_id':base['Id'],'base_repo_digests':base.get('RepoDigests',[]),
            'image_id':image['Id'],'context':args.context,'tag':args.tag,'daemon_id':daemon['ID']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
