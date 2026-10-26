"""Verify in quarantine before publishing one fixed application-owned Git ref."""
from pathlib import Path

from naiyue_test_agent.contracts.git_inputs import GitInputError,git_path,input_path
from naiyue_test_agent.contracts.git_results import result_receipt,result_ref
from naiyue_test_agent.contracts.projects import LocalSource
from naiyue_test_agent.storage.files import check_store_directory
from .git_repository import GitRepository,bundle_digest,check_cache_budget
from .git_snapshot import validate_tree


class ResultImporter:
    def __init__(self,directory,settings):
        self.directory,self.settings=Path(directory).absolute(),settings
        self.repository=GitRepository(self.directory/'objects.git',settings)

    def verify(self,path,receipt,expected):
        result_receipt(receipt)
        if any(receipt[key]!=expected[key] for key in
                ('binding_id','project_id','session_id','input_id','original_head','input_snapshot')):
            raise GitInputError('git_result_binding_mismatch')
        if len(receipt['excluded'])>self.settings.max_files:raise GitInputError('git_result_budget')
        size,digest=bundle_digest(path,self.settings.max_bundle_bytes)
        if size!=receipt['bundle_size'] or digest!=receipt['bundle_sha256']:
            raise GitInputError('git_result_corrupt')
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=True);check_store_directory(self.directory)
        repo=self.repository;repo.run(['init','--bare','--quiet',str(repo.path)],cwd=self.directory)
        heads=repo.run(['bundle','list-heads',str(path)]).decode().splitlines()
        ref=result_ref(receipt['result_id'])
        if heads!=[receipt['output_commit']+' '+ref]:raise GitInputError('git_result_invalid_bundle')
        repo.run(['bundle','verify',str(path)])
        repo.run(['fetch','--no-tags','--no-recurse-submodules','--no-write-fetch-head',str(path),ref+':'+ref])
        repo.run(['fsck','--strict','--no-reflogs'])
        if (repo.oid(ref+'^{commit}')!=receipt['output_commit'] or not repo.has(receipt['input_snapshot'])
                or repo.run(['merge-base','--is-ancestor',receipt['input_snapshot'],receipt['output_commit']],check=False)[0]):
            raise GitInputError('git_result_not_descendant')
        manifest,_=validate_tree(repo,receipt['output_commit'])
        check_cache_budget(self.directory,self.settings.max_cache_bytes)
        folded={};warnings=[]
        for item in manifest:
            key=item.path.casefold()
            if key in folded:warnings.append(dict(code='case_conflict',paths=[folded[key],item.path]))
            folded[key]=item.path
        return dict(files=self.files(receipt),warnings=warnings)

    def files(self,receipt):
        raw=self.repository.run(['diff','--no-ext-diff','--no-textconv','--no-abbrev','--raw','-z',
            '-M',receipt['input_snapshot'],receipt['output_commit'],'--'])
        parts=raw.split(b'\0');index=0;rows=[]
        while index<len(parts)-1:
            old_mode,new_mode,old_oid,new_oid,status=parts[index].decode().lstrip(':').split();index+=1
            old_path=git_path(parts[index]);index+=1;path=old_path
            if status[0] in ('R','C'):path=git_path(parts[index]);index+=1
            rows.append(dict(path=path,old_path=old_path,status=status[0],old_mode=old_mode,new_mode=new_mode,
                old_oid=old_oid,new_oid=new_oid,symlink='120000' in (old_mode,new_mode),
                attributes=path.endswith('.gitattributes')))
        if len(rows)>self.settings.max_files:raise GitInputError('git_result_budget')
        return rows

    def publish(self,receipt,source):
        ref=result_ref(receipt['result_id']);output=receipt['output_commit']
        if not isinstance(source,LocalSource):return str(self.repository.path)
        repo=self.destination(source)
        root=source.path
        if repo.run(['symbolic-ref','--quiet',ref],check=False)[0]==0:
            raise GitInputError('git_result_ref_conflict')
        status,current=repo.run(['rev-parse','--verify',ref],check=False)
        if not status:
            if current.decode().strip()!=output:raise GitInputError('git_result_ref_conflict')
            return str(root)
        # Transfer only verified objects. Local fetch would honor URL rewrites and
        # alternateRefsCommand from the user's repository, even for a local URL.
        pack=self.repository.run(['pack-objects','--stdout','--revs'],data=(output+'\n').encode(),
            output_limit=self.settings.max_bundle_bytes)
        repo.run(['index-pack','--stdin','--strict'],data=pack,extra_env={'GIT_ALLOW_PROTOCOL':'file'})
        repo.run(['update-ref','--no-deref',ref,output,'0'*40])
        return str(root)

    def destination(self,source):
        if not isinstance(source,LocalSource):return self.repository
        root=source.path
        check_store_directory(root)
        if not (root/'.git').is_dir() or (root/'.git').is_symlink():raise GitInputError('git_result_layout_changed')
        repo=GitRepository(root,self.settings)
        if Path(repo.run(['rev-parse','--show-toplevel']).decode().strip()).resolve()!=root.resolve():
            raise GitInputError('git_result_layout_changed')
        return repo

    def diff(self,receipt,path):
        input_path(path)
        entry=next((row for row in self.files(receipt) if row['path']==path),None)
        if entry is None:raise GitInputError('git_result_file_not_found')
        args=['--literal-pathspecs','diff','--no-ext-diff','--no-textconv','--no-color','-M',
            receipt['input_snapshot'],receipt['output_commit'],'--',entry['old_path']]
        if entry['old_path']!=path:args.append(path)
        stats=self.repository.run(args[:2]+['--numstat','-z']+args[2:])
        binary=stats.startswith(b'-\t-\t')
        patch=None if binary else self.repository.run(args[:2]+['--patch']+args[2:]).decode('utf-8',errors='replace')
        return dict(path=path,binary=binary,patch=patch,**{key:entry[key] for key in ('status','old_path','old_mode','new_mode','symlink','attributes')})
