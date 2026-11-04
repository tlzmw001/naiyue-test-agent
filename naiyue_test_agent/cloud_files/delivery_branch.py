"""Create one explicit branch through private objects/index and a new-ref CAS."""
import hashlib,json,logging,os,re,tempfile
from pathlib import Path
from uuid import uuid4
from pydantic import ValidationError
from naiyue_test_agent.contracts.cloud_sources import SourceReceipt
from naiyue_test_agent.contracts.git_native import GitInputError
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import check_store_directory,check_store_file,open_store_readonly,open_store_file,sync_store_directory
from .archive import strict_json
from .paths import source_handle,identity
from .delivery_git import DeliveryGit,oid,quoted

logger=logging.getLogger(__name__)


def _read(path,budget):
    with os.fdopen(open_store_readonly(path),'rb') as stream:
        before=identity(os.fstat(stream.fileno()));raw=stream.read(budget+1)
        if len(raw)>budget or identity(os.fstat(stream.fileno()))!=before:raise CloudError('cloud_delivery_source_changed',409)
        return raw


def _ref(repository,ref):
    gitdir=Path(repository.run(['rev-parse','--absolute-git-dir']).decode().strip());check_store_directory(gitdir)
    path=gitdir/ref;parent=path.parent
    while not os.path.lexists(parent):parent=parent.parent
    check_store_directory(parent)
    if os.path.lexists(path):check_store_file(path)
    code,_=repository.run(['symbolic-ref','--quiet','--no-recurse',ref],check=False)
    if code==0:raise CloudError('cloud_delivery_branch_conflict',409)
    if code!=1:raise CloudError('cloud_delivery_git_failed',409)
    code,_=repository.run(['show-ref','--verify','--quiet',ref],check=False)
    if code==1:return None
    if code:raise CloudError('cloud_delivery_git_failed',409)
    return oid(repository.run(['show-ref','--verify','--hash',ref]))


def _policy(git,verified,receipt,stage,limits):
    policy=verified.selection.policy
    if policy is None:raise CloudError('cloud_delivery_source_receipt_required',409)
    work=stage/'attributes';work.mkdir(mode=0o700);global_attrs=stage/'global-attributes';global_attrs.write_bytes(b'')
    for rule in policy.rules:
        if rule.kind!='attributes':continue
        if rule.name=='global/attributes':target=global_attrs
        elif rule.name=='repository/info/attributes':target=git.path/'info/attributes'
        elif rule.name.startswith('worktree/') and rule.name.endswith('.gitattributes'):
            target=work/rule.name.removeprefix('worktree/')
        else:raise CloudError('cloud_delivery_attributes_unsupported',409)
        target.parent.mkdir(mode=0o700,parents=True,exist_ok=True);target.write_text(rule.content,encoding='utf-8',newline='')
    paths=[entry.path for entry in verified.output_manifest.entries]
    policy_env={'GIT_INDEX_FILE':str(stage/'attributes.index')}
    git.run(['read-tree','--empty'],env=policy_env)
    raw=git.run(['-c','core.attributesFile='+str(global_attrs),'--work-tree='+str(work),
        'check-attr','-z','--stdin','text','eol','filter','working-tree-encoding','ident'],data=b''.join(p.encode()+b'\0' for p in paths),env=policy_env)
    fields=raw.split(b'\0')[:-1];attrs={}
    if len(fields)%3:raise CloudError('cloud_delivery_git_invalid',409)
    for i in range(0,len(fields),3):attrs.setdefault(fields[i].decode(),{})[fields[i+1].decode()]=fields[i+2].decode()
    safe={'unset','unspecified'};lines=[]
    for path in paths:
        values=attrs.get(path,{})
        if (set(values)!={'text','eol','filter','working-tree-encoding','ident'}
                or any(values[key] not in safe for key in ('filter','working-tree-encoding','ident'))
                or values['text'] not in safe|{'set','auto'} or values['eol'] not in safe|{'lf','crlf'}):
            raise CloudError('cloud_delivery_attributes_unsupported',409)
        text={'set':'text','unset':'-text','unspecified':'!text','auto':'text=auto'}[values['text']]
        eol={'unset':'-eol','unspecified':'!eol','lf':'eol=lf','crlf':'eol=crlf'}[values['eol']]
        pattern='/'+path.replace('[','\\[').replace(']','\\]')
        lines.append(quoted(pattern)+' '+text+' '+eol+' -filter -working-tree-encoding -ident\n')
    # Highest-precedence per-path rules prevent O's changed attrs from activating a filter.
    (git.path/'info').mkdir(mode=0o700,exist_ok=True)
    (git.path/'info/attributes').write_text(''.join(lines),encoding='utf-8',newline='')
    config=[]
    for key,allowed in (('core.autocrlf',{'false','true','input'}),('core.eol',{'lf','crlf','native'})):
        value=receipt.config.get(key,'false' if key=='core.autocrlf' else 'native').lower()
        if value not in allowed:raise CloudError('cloud_delivery_attributes_unsupported',409)
        config.extend(['-c',key+'='+value])
    return config


def _output(git,verified,work,config,limits):
    work.mkdir(mode=0o700);regular=[];links=[];total=0
    for entry in verified.output_manifest.entries:
        path=work/entry.path;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);sha=hashlib.sha256();size=0
        source=verified.directory/entry.path
        with source_handle(verified.directory,entry.path,identity(source.lstat())) as (stream,info),path.open('xb') as output:
            if entry.type=='symlink':
                if info is not None or stream!=entry.target.encode():raise CloudError('cloud_delivery_source_changed',409)
                sha.update(stream);size=len(stream);output.write(stream);links.append(entry)
            else:
                if info is None:raise CloudError('cloud_delivery_source_changed',409)
                while chunk:=stream.read(min(256*1024,entry.size-size+1)):
                    size+=len(chunk);sha.update(chunk)
                    if size>entry.size:raise CloudError('cloud_delivery_source_changed',409)
                    output.write(chunk)
                regular.append(entry)
        total+=size
        if size!=entry.size or sha.hexdigest()!=entry.sha256 or total>limits.max_result_bytes:raise CloudError('cloud_delivery_source_changed',409)
    args=[*config,'--work-tree='+str(work)]
    hashes=git.hashes([e.path for e in regular],raw=False,cwd=work,args=args)
    result={entry.path:value for entry,value in zip(regular,hashes)}
    hashes=git.hashes([e.path for e in links],cwd=work)
    result.update({entry.path:value for entry,value in zip(links,hashes)})
    return result


def _prepare(verified,receipt_path,source_root,name,limits,stage):
    source_root=Path(source_root).absolute();check_store_directory(source_root)
    raw=_read(receipt_path,limits.max_metadata_bytes*4);strict_json(raw)
    receipt=SourceReceipt.model_validate_json(raw)
    if receipt.root!=source_root or receipt.head is None:raise CloudError('cloud_delivery_source_receipt_required',409)
    parent=oid(receipt.head)
    if (not isinstance(name,str) or not 1<=len(name)<=128 or name.startswith(('-','refs/')) or name=='HEAD'
            or re.search(r'[\x00-\x20\x7f~^:?*\[\\]',name) or '..' in name or '@{' in name):
        raise CloudError('cloud_delivery_branch_invalid',422)
    ref='refs/heads/'+name;repository=DeliveryGit.repository(source_root,limits)
    if repository.run(['check-ref-format',ref],check=False)[0]:raise CloudError('cloud_delivery_branch_invalid',422)
    roots=repository.run(['rev-parse','--show-toplevel','--absolute-git-dir','--git-path','objects','--show-object-format']).decode().splitlines()
    if len(roots)!=4 or Path(roots[0]).resolve()!=source_root.resolve() or roots[3] not in ('sha1','sha256'):
        raise CloudError('cloud_delivery_source_receipt_required',409)
    gitdir=Path(roots[1]);check_store_directory(gitdir)
    object_dir=Path(roots[2]);object_dir=object_dir if object_dir.is_absolute() else source_root/object_dir
    check_store_directory(object_dir)
    if repository.run(['cat-file','-t',parent]).strip()!=b'commit':raise CloudError('cloud_delivery_source_receipt_required',409)
    git=DeliveryGit(stage/'git',limits,object_format=roots[3],alternate=object_dir)
    git.run(['read-tree',parent]);base=git.listing(parent)
    managed={entry.path for entry in verified.input_manifest.entries}|set(receipt.deleted)|{entry.path for entry in verified.output_manifest.entries}
    for path in managed:
        if any(path==sub or path.startswith(sub+'/') for sub in receipt.submodules):raise CloudError('cloud_delivery_path_conflict',409)
    for path in {e.path for e in verified.output_manifest.entries}:
        if any(old not in managed and (old.startswith(path+'/') or path.startswith(old+'/')) for old in base):
            raise CloudError('cloud_delivery_path_conflict',409)
    config=_policy(git,verified,receipt,stage,limits)
    hashes=_output(git,verified,stage/'output',config,limits)
    git.update([(path,'0','0'*len(parent)) for path in sorted(managed)])
    git.update([(entry.path,'120000' if entry.type=='symlink' else '100755' if entry.mode==0o755 else '100644',hashes[entry.path])
        for entry in verified.output_manifest.entries])
    tree=oid(git.run(['write-tree']))
    message=('Cloud task result\n\ninput '+verified.input_manifest.input_id+'\noutput '+verified.output_manifest.sha256+'\nbranch '+ref+'\n').encode()
    env={key:value for prefix in ('AUTHOR','COMMITTER') for key,value in (
        ('GIT_'+prefix+'_NAME','Naiyue Cloud Task'),('GIT_'+prefix+'_EMAIL','naiyue@localhost'),('GIT_'+prefix+'_DATE','@0 +0000'))}
    commit=oid(git.run(['-c','commit.gpgSign=false','commit-tree',tree,'-p',parent],data=message,env=env))
    plan=dict(ref=ref,parent=parent,tree=tree,commit=commit,input_id=verified.input_manifest.input_id,output_sha256=verified.output_manifest.sha256)
    return git,repository,plan


def _journal(path,plan,limits):
    if path is None:return
    path=Path(path);check_store_directory(path.parent)
    stage=path.with_name(path.name+'.'+uuid4().hex+'.next');fd=open_store_file(stage,exclusive_create=True)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(json.dumps(plan,sort_keys=True).encode());stream.flush();os.fsync(stream.fileno())
        try:os.link(stage,path,follow_symlinks=False)
        except FileExistsError:pass  # The retained original plan is checked below, never replaced.
    finally:stage.unlink()
    with os.fdopen(open_store_file(path),'rb') as stream:
        before=identity(os.fstat(stream.fileno()));raw=stream.read(limits.max_metadata_bytes+1)
        if (len(raw)>limits.max_metadata_bytes or strict_json(raw)!=plan
                or identity(os.fstat(stream.fileno()))!=before):raise CloudError('cloud_delivery_branch_conflict',409)
        os.fsync(stream.fileno())
    sync_store_directory(path.parent)


def branch_preflight(verified,receipt_path,source_root,name,limits):
    from .delivery_paths import portable_conflicts
    invalid=portable_conflicts(e.path for e in verified.output_manifest.entries)
    if invalid:return invalid
    try:
        with tempfile.TemporaryDirectory(prefix='branch-',dir=verified.directory.parent) as temporary:
            _,repository,plan=_prepare(verified,receipt_path,source_root,name,limits,Path(temporary))
            if _ref(repository,plan['ref']) is not None:raise CloudError('cloud_delivery_branch_conflict',409)
        return []
    except (OSError,ValueError,ValidationError,GitInputError) as error:
        logger.warning('cloud_delivery_branch_preflight_failed error_type=%s',type(error).__name__)
        raise CloudError('cloud_delivery_branch_failed',409) from error


def branch_delivery(verified,receipt_path,source_root,name,limits,*,journal=None):
    from .delivery_paths import portable_conflicts
    if portable_conflicts(e.path for e in verified.output_manifest.entries):raise CloudError('cloud_delivery_path_unsupported',422)
    try:
        with tempfile.TemporaryDirectory(prefix='branch-',dir=verified.directory.parent) as temporary:
            stage=Path(temporary);git,repository,plan=_prepare(verified,receipt_path,source_root,name,limits,stage)
            _journal(journal,plan,limits)
            existing=_ref(repository,plan['ref'])
            if existing is not None:
                if existing!=plan['commit']:raise CloudError('cloud_delivery_branch_conflict',409)
                return {'branch_head':existing}
            pack=stage/'objects.pack'
            with pack.open('xb') as output:
                git.run(['pack-objects','--stdout','--revs'],data=(plan['commit']+'\n^'+plan['parent']+'\n').encode(),
                    output_stream=output,output_limit=limits.max_result_bytes+limits.max_metadata_bytes*8)
            with pack.open('rb') as stream:repository.run(['index-pack','--stdin','--strict'],input_stream=stream)
            repository.run(['update-ref','--no-deref',plan['ref'],plan['commit'],'0'*len(plan['parent'])])
            if _ref(repository,plan['ref'])!=plan['commit']:raise CloudError('cloud_delivery_branch_unconfirmed',409)
            return {'branch_head':plan['commit']}
    except (OSError,ValueError,ValidationError,GitInputError) as error:
        logger.warning('cloud_delivery_branch_failed error_type=%s',type(error).__name__)
        raise CloudError('cloud_delivery_branch_failed',409) from error
