"""Finite local Git transitions with explicit plans and read-only reconciliation."""
from pathlib import Path

from naiyue_test_agent.contracts.git_delivery import GitDeliveryError,branch_name
from naiyue_test_agent.contracts.git_inputs import git_path
from naiyue_test_agent.contracts.git_results import result_ref
from naiyue_test_agent.contracts.projects import LocalSource
from naiyue_test_agent.storage.files import check_store_directory
from .git_snapshot import validate_tree,check_filters


class GitDelivery:
    def __init__(self,importer,source,receipt,settings):
        self.importer,self.source,self.receipt,self.settings=importer,source,receipt,settings
        self.repository=importer.destination(source)
        self.gitdir=Path(self.repository.run(['rev-parse','--absolute-git-dir']).decode().strip())
        check_store_directory(self.gitdir)

    def _ref(self,ref):
        for parent in (self.gitdir/ref).parents:
            if parent==self.gitdir:break
            if parent.is_symlink():raise GitDeliveryError('git_delivery_unsafe_layout')
        if (self.gitdir/ref).is_symlink() or self.repository.run(['symbolic-ref','--quiet',ref],check=False)[0]==0:
            raise GitDeliveryError('git_delivery_symbolic_ref')
        status,value=self.repository.run(['rev-parse','--verify',ref],check=False)
        return None if status else value.decode().strip()

    def _result(self):
        if self._ref(result_ref(self.receipt['result_id']))!=self.receipt['output_commit']:
            raise GitDeliveryError('git_delivery_result_ref_changed')

    def _head(self):
        status,branch=self.repository.run(['symbolic-ref','--quiet','--no-recurse','HEAD'],check=False)
        if status or not branch.decode().strip().startswith('refs/heads/'):
            raise GitDeliveryError('git_delivery_detached_head')
        ref=branch.decode().strip();return ref,self._ref(ref)

    def _clean(self,paths=()):
        for name in ('MERGE_HEAD','CHERRY_PICK_HEAD','REVERT_HEAD','rebase-merge','rebase-apply','sequencer','index.lock'):
            path=self.gitdir/name
            if path.exists() or path.is_symlink():raise GitDeliveryError('git_delivery_unfinished_git')
        attributes=self.gitdir/'info'/'attributes'
        if attributes.exists() or attributes.is_symlink():raise GitDeliveryError('git_delivery_local_attributes')
        tracked=[git_path(path) for path in self.repository.run(['ls-files','-z']).split(b'\0') if path]
        # check-attr reads attributes without executing clean/process filters.
        # Include future checkout paths as well as the current index.
        check_filters(self.repository,sorted(set(tracked)|set(paths)))
        if self.repository.run(['-c','core.untrackedCache=false','status','--porcelain=v1','--untracked-files=no']):
            raise GitDeliveryError('git_delivery_dirty')

    def _checkout_tree(self,commit):
        manifest,_=validate_tree(self.importer.repository,commit)
        folded=set()
        for item in manifest:
            key=item.path.casefold()
            if key in folded:raise GitDeliveryError('git_delivery_case_conflict')
            folded.add(key)
        return [item.path for item in manifest]

    def _merge_plan(self):
        if not isinstance(self.source,LocalSource):raise GitDeliveryError('git_delivery_no_worktree')
        receipt=self.receipt;repo=self.repository
        branch,head=self._head()
        if head!=receipt['original_head']:raise GitDeliveryError('git_delivery_head_changed')
        self._clean()
        if repo.oid(receipt['input_snapshot']+'^{tree}')!=repo.oid(head+'^{tree}'):
            raise GitDeliveryError('git_delivery_input_wip')
        if receipt['input_snapshot']!=head:raise GitDeliveryError('git_delivery_input_history')
        lines=self.importer.repository.run(['rev-list','--reverse','--parents',
            receipt['output_commit'],'^'+receipt['input_snapshot']]).decode().splitlines()
        if len(lines)>self.settings.max_commits:raise GitDeliveryError('git_delivery_history_budget')
        previous=head;commits=[];paths=set()
        for line in lines:
            fields=line.split()
            if len(fields)!=2 or fields[1]!=previous:raise GitDeliveryError('git_delivery_nonlinear')
            previous=fields[0];commits.append(previous);paths.update(self._checkout_tree(previous))
        if previous!=receipt['output_commit']:raise GitDeliveryError('git_delivery_nonlinear')
        self._checkout_tree(head)
        check_filters(repo,sorted(paths))
        others=[]
        for flags in (['--others','--exclude-standard'],['--others','--ignored','--exclude-standard']):
            others.extend(git_path(p) for p in repo.run(['ls-files',*flags,'-z']).split(b'\0') if p)
        for path in others:
            if any(path.casefold()==target.casefold() or path.casefold().startswith(target.casefold()+'/')
                    or target.casefold().startswith(path.casefold()+'/') for target in paths):
                raise GitDeliveryError('git_delivery_untracked_collision')
        return dict(action='merge',branch=branch,before=head,target=receipt['output_commit'],commits=commits)

    def plan(self,action,*,branch=None):
        self._result();target=self.receipt['output_commit']
        if action=='branch':
            ref='refs/heads/'+branch_name(branch)
            if self.repository.run(['check-ref-format',ref],check=False)[0]:raise GitDeliveryError('git_delivery_invalid_branch')
            if self._ref(ref) is not None:raise GitDeliveryError('git_delivery_branch_exists')
            return dict(action=action,ref=ref,before=None,target=target)
        if action=='discard':return dict(action=action,ref=result_ref(self.receipt['result_id']),before=target,target=None)
        if action=='merge':return self._merge_plan()
        raise GitDeliveryError('git_delivery_invalid_action')

    def execute(self,plan,*,on_progress=lambda _oid:None):
        repo=self.repository;self._result()
        if plan['action']=='branch':
            if self._ref(plan['ref']) is not None:raise GitDeliveryError('git_delivery_branch_exists')
            repo.run(['update-ref','--no-deref',plan['ref'],plan['target'],'0'*40])
        elif plan['action']=='discard':
            repo.run(['update-ref','--no-deref','-d',plan['ref'],plan['before']])
        elif plan['action']=='merge':
            expected=plan['before']
            for oid in plan['commits']:
                branch,head=self._head()
                if branch!=plan['branch'] or head!=expected:raise GitDeliveryError('git_delivery_head_changed')
                self._clean(self._checkout_tree(oid))
                repo.run(['-c','commit.gpgSign=false','-c','tag.gpgSign=false','-c','merge.renormalize=false',
                    'cherry-pick','--ff','--no-edit',oid],extra_env={'GIT_NO_LAZY_FETCH':'1','GIT_ALLOW_PROTOCOL':'file'})
                branch,head=self._head()
                if branch!=plan['branch'] or head!=oid:raise GitDeliveryError('git_delivery_unconfirmed')
                self._clean();on_progress(oid);expected=oid
        else:raise GitDeliveryError('git_delivery_invalid_action')
        return self.reconcile(plan)

    def reconcile(self,plan):
        if plan['action'] in ('branch','discard'):
            matches=self._ref(plan['ref'])==plan['target']
            return dict(state='completed' if matches else 'unknown',target=plan['target'])
        branch,head=self._head()
        try:self._clean();clean=True
        except GitDeliveryError:clean=False
        return dict(state='completed' if branch==plan['branch'] and head==plan['target'] and clean else 'unknown',
            target=plan['target'],actual_head=head)
