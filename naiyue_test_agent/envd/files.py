"""Reuse the safe file algorithms inside the container's own namespace."""
from pathlib import Path
from langchain_core.tools import ToolException
from naiyue_test_agent.execution.host_files import HostFileSystem
from naiyue_test_agent.execution_roots import ExecutionRoot
from naiyue_test_agent.contracts.environment import ResourceBoundaryError


class EnvdFiles(HostFileSystem):
    def __init__(self, environment, session_id):
        super().__init__(ExecutionRoot(Path(environment.root)),None,session_id=session_id)
        object.__setattr__(self,'environment',environment)

    def _safe(self,path,*,resource=False):
        root=self.execution_root.path
        target=Path(path)
        target=target if target.is_absolute() else root/target
        try:
            if not target.resolve().is_relative_to(root.resolve()):
                if resource:raise ResourceBoundaryError('outside_workspace')
                raise ToolException('File target is outside the sandbox execution root.')
        except (OSError,RuntimeError) as error:
            raise ToolException('Sandbox file target is unavailable.') from error
        return target

    def prepare_target(self,path,operation):
        self._safe(path)
        subject=super().prepare_target(path,operation)
        self._safe(subject['resolved'])
        return subject

    def inspect_candidate(self,path,operation):
        return self.prepare_target(path,operation)

    def verify_target(self,subject):
        if (subject.get('workspace')!=self.environment.root or
                subject.get('execution_environment',self.environment.public())!=self.environment.public()):
            raise ToolException('Sandbox file subject differs from its environment.')
        self._safe(subject['requested'])
        self._safe(subject['resolved'])
        target=super().verify_target(subject)
        self._safe(target)
        return target

    def resource_info(self,path):
        self._safe(path,resource=True)
        return super().resource_info(path)

    def resolve_resource(self,path):
        self._safe(path,resource=True)
        return super().resolve_resource(path)

    def list_resources(self,path):
        self._safe(path,resource=True)
        return super().list_resources(path)

    def read_resource(self,path,max_bytes):
        self._safe(path,resource=True)
        return super().read_resource(path,max_bytes)
