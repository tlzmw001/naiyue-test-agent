"""Verify an extracted acceptance kit and optionally the installed application."""
import argparse,hashlib,json
from pathlib import Path,PurePosixPath


def safe_file(root,name):
    parts=PurePosixPath(name)
    if (not name or parts.is_absolute() or parts.as_posix()!=name or '\\' in name
            or any(p in ('','.','..') or ':' in p for p in parts.parts)):
        raise ValueError('candidate_path_invalid')
    cursor=root
    for part in parts.parts:
        cursor=cursor/part
        if cursor.is_symlink() or getattr(cursor,'is_junction',lambda:False)():raise ValueError('candidate_path_invalid')
    if not cursor.is_file():raise ValueError('candidate_file_missing')
    return cursor


def sha(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        while data:=stream.read(256*1024):value.update(data)
    return value.hexdigest()


def python_sha(data):
    # Match the standard release inspector: only Python source line endings are normalized.
    return hashlib.sha256(data.replace(b'\r\n',b'\n')).hexdigest()


def verify(root,*,installed=False):
    root=Path(root).absolute()
    if root.is_symlink() or getattr(root,'is_junction',lambda:False)():raise ValueError('candidate_path_invalid')
    manifest=json.loads(safe_file(root,'candidate.json').read_text(encoding='utf-8'))
    if manifest.get('schema')!=1:raise ValueError('candidate_schema_invalid')
    for name,value in manifest['files'].items():
        path=safe_file(root,name)
        if (path.stat().st_size,sha(path))!=(value['size'],value['sha256']):raise ValueError('candidate_file_mismatch:'+name)
    for path in root.rglob('*'):
        if '__pycache__' in path.parts:continue
        name=path.relative_to(root).as_posix()
        if path.is_symlink() or getattr(path,'is_junction',lambda:False)():raise ValueError('candidate_path_invalid')
        if path.is_file() and name not in {*manifest['files'],'candidate.json','SHA256SUMS'}:raise ValueError('candidate_unlisted_file:'+name)
    checked=0
    if installed:
        import naiyue_test_agent
        base=Path(naiyue_test_agent.__file__).parent
        for name,expected in manifest['installed_files'].items():
            path=safe_file(base,name)
            actual=python_sha(path.read_bytes()) if name.endswith('.py') and manifest.get('python_newlines')=='lf' else sha(path)
            if actual!=expected:raise ValueError('candidate_installed_mismatch:'+name)
            checked+=1
    return dict(status='passed',candidate_id=manifest.get('candidate_id'),files=len(manifest['files']),installed_files=checked)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--installed',action='store_true');args=parser.parse_args()
    print(json.dumps(verify(args.root,installed=args.installed),indent=2))


if __name__=='__main__':main()
