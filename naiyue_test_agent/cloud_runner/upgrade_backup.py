"""Bounded raw backup in an isolated maintenance container after writer removal."""
import argparse,hashlib,json,os,re,stat
from pathlib import Path,PurePosixPath
from uuid import uuid4
from naiyue_test_agent.storage.raw_tree import copy_roots

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_releases import BackupRequest,UpgradeBackup
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.storage.files import open_store_file,open_store_readonly,check_store_directory,sync_store_directory
from naiyue_test_agent.cloud_install.store import directory,read,write


def _verified_existing(destination,request,max_bytes,max_files):
    complete=read(destination/'complete.json')
    if complete.get('request')!=request or not re.fullmatch(r'attempt-[a-f0-9]{32}',complete.get('payload','')):
        raise CloudError('upgrade_backup_invalid')
    result=UpgradeBackup.model_validate(complete['result']).model_dump();root=destination/complete['payload']
    check_store_directory(root)
    manifest=read(root/'manifest.json',max(1024*1024,max_files*8192))
    if digest(manifest)!=result['manifest_sha256'] or manifest.get('request')!=request:raise CloudError('upgrade_backup_invalid')
    if len(manifest['entries'])>max_files:raise CloudError('upgrade_backup_invalid')
    expected={'manifest.json','workspace','state'};size=count=0
    for item in manifest['entries']:
        path=PurePosixPath(item['path'])
        if path.is_absolute() or '..' in path.parts or path.parts[0] not in ('workspace','state'):raise CloudError('upgrade_backup_invalid')
        expected.add(path.as_posix());file=root/path
        check_store_directory(file if item['kind']=='directory' else file.parent)
        if item['kind']=='file':
            sha=hashlib.sha256();current=0
            with os.fdopen(open_store_readonly(file),'rb') as stream:
                while data:=stream.read(256*1024):
                    sha.update(data);size+=len(data);current+=len(data)
                    if size>max_bytes:raise CloudError('upgrade_backup_invalid')
            if current!=item['size'] or sha.hexdigest()!=item['sha256']:raise CloudError('upgrade_backup_invalid')
            count+=1
    actual={p.relative_to(root).as_posix() for p in root.rglob('*')}
    if actual!=expected or (count,size)!=(result['files'],result['bytes']):raise CloudError('upgrade_backup_invalid')
    return result


def backup(workspace,state,destination,request,*,max_bytes,max_files):
    request=BackupRequest.model_validate(request).model_dump();destination=directory(destination)
    if (destination/'complete.json').exists():return _verified_existing(destination,request,max_bytes,max_files)
    attempts=list(destination.iterdir())
    if len(attempts)>=3:raise CloudError('upgrade_backup_incomplete')
    payload=directory(destination/('attempt-'+uuid4().hex))
    roots=[]
    for name,path in [('workspace',workspace),('state',state)]:
        roots.append((name,Path(path),directory(payload/name)))
    records=copy_roots(roots,max_bytes=max_bytes,max_files=max_files)
    manifest=dict(schema_version=1,request=request,entries=records)
    write(payload/'manifest.json',manifest)
    files=[v for v in records if v['kind']=='file']
    result=UpgradeBackup(**request,manifest_sha256=digest(manifest),files=len(files),bytes=sum(v['size'] for v in files)).model_dump()
    write(destination/'complete.json',dict(request=request,result=result,payload=payload.name))
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--request',required=True);p.add_argument('--max-bytes',type=int,required=True);p.add_argument('--max-files',type=int,required=True)
    args=p.parse_args()
    try:
        result=backup(Path('/workspace'),Path('/state'),Path('/backup'),json.loads(args.request),max_bytes=args.max_bytes,max_files=args.max_files)
        print(json.dumps(result));return 0
    except (CloudError,OSError,ValueError) as error:
        print(json.dumps(dict(error=error.code if isinstance(error,CloudError) else 'upgrade_backup_failed')));return 1


if __name__=='__main__':raise SystemExit(main())
