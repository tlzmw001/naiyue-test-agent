"""Privileged file service: all workspace access is descriptor-relative."""
import os
from contextlib import contextmanager
from pathlib import Path
import stat
from langchain_core.tools import ToolException
from naiyue_test_agent.execution.host_files import HostFileSystem
from naiyue_test_agent.execution_roots import ExecutionRoot
from naiyue_test_agent.contracts.environment import FileResource,ResourceBoundaryError
from naiyue_test_agent.contracts.sandbox import TASK_UID,TASK_GID
from .workspace import Workspace,WorkspaceOutside


class EnvdFiles(HostFileSystem):
    def __init__(self, environment, session_id):
        super().__init__(ExecutionRoot(Path(environment.root)),None,session_id=session_id)
        object.__setattr__(self,'environment',environment)
        object.__setattr__(self,'workspace',Workspace(environment.root))
        object.__setattr__(self,'owner',(TASK_UID,TASK_GID) if os.geteuid() == 0 else None)

    def check_subject(self,subject):
        if (subject.get('workspace') != self.environment.root or
                subject.get('execution_environment',self.environment.public()) != self.environment.public()):
            raise ToolException('Sandbox file subject differs from its environment.')

    def prepare_target(self,path,operation):
        return self.workspace.subject(path,operation)

    def inspect_candidate(self,path,operation):
        return self.prepare_target(path,operation)

    def verify_target(self,subject):
        self.check_subject(subject)
        with self.workspace.open(subject['requested'],missing=subject['operation']=='write') as target:
            self.workspace.check(target,subject)
            return self.workspace.public(target.relative)

    def read(self,subject,offset,limit,*,settings,control,progress):
        from naiyue_test_agent.tools.read import _read
        self.check_subject(subject)
        with self.workspace.open(subject['requested']) as target:
            self.workspace.check(target,subject)
            with os.fdopen(os.dup(target.fd),'rb') as source:
                return _read(None,offset,limit,settings=settings,control=control,progress=progress,source=source)

    def write(self,subject,content,*,settings,control,progress,read_allowed):
        from .file_mutations import mutate
        return mutate(self,subject,content=content,settings=settings,control=control,
                      progress=progress,read_allowed=read_allowed)

    def edit(self,subject,edits,*,settings,control,progress):
        from .file_mutations import mutate
        return mutate(self,subject,edits=edits,settings=settings,control=control,progress=progress)

    def list(self,subject,limit,access,*,settings,progress):
        from naiyue_test_agent.tools.ls import _list_directory
        self.check_subject(subject)
        with self.workspace.open(subject['requested']) as target:
            self.workspace.check(target,subject)
            return _list_directory(Path(subject['resolved']),limit,access,settings=settings,progress=progress,
                                   directory_fd=target.fd,entry_stat=self._entry_stat)

    def _entry_stat(self,path):
        with self.workspace.open(path) as target:
            return target.info

    async def find(self,subject,pattern,limit,access,*,settings,process_settings,progress,timeout):
        from naiyue_test_agent.tools.find import _find_files
        from .identity import TaskProcessRunner
        verified=Path(self.verify_target(subject))
        target=self.workspace.physical/verified.relative_to(self.workspace.root)
        return await _find_files(target,pattern,limit,access,settings=settings,
            runner=TaskProcessRunner(process_settings),progress=progress,timeout=timeout)

    async def grep(self,subject,pattern,glob,ignore_case,literal,context_lines,limit,access,
                   *,settings,process_settings,progress,timeout):
        from naiyue_test_agent.tools.grep import _grep_files
        from .identity import TaskProcessRunner
        return await _grep_files(Path(self.verify_target(subject)),pattern,glob,ignore_case,literal,context_lines,limit,
            access,settings=settings,runner=TaskProcessRunner(process_settings),progress=progress,timeout=timeout)

    def resource_info(self,path):
        with self._resource(path,missing=True) as target:
            if target.info is None:
                return FileResource(False,target.linked,'missing')
            return FileResource(True,target.linked,'directory' if stat.S_ISDIR(target.info.st_mode) else 'file')

    def resolve_resource(self,path):
        with self._resource(path) as target:
            return self.workspace.public(target.relative)

    def list_resources(self,path):
        with self._resource(path) as target:
            if not stat.S_ISDIR(target.info.st_mode):
                raise NotADirectoryError(path)
            return tuple(self.workspace.public(str(Path(target.relative)/name)) for name in os.listdir(target.fd))

    def read_resource(self,path,max_bytes):
        with self._resource(path) as target:
            if not stat.S_ISREG(target.info.st_mode):raise OSError('Resource is not a regular file.')
            with os.fdopen(os.dup(target.fd),'rb') as source:
                return source.read(max_bytes+1)

    @contextmanager
    def _resource(self,path,*,missing=False):
        try:
            with self.workspace.open(path,missing=missing) as target:
                yield target
        except WorkspaceOutside as error:
            raise ResourceBoundaryError('outside_workspace') from error
        except ToolException as error:
            raise OSError('Sandbox resource is unavailable.') from error
