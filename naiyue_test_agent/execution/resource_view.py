"""Ephemeral metadata/body view for one resource preparation, never a disk cache."""
import base64

from naiyue_test_agent.contracts.environment import FileResource,ResourceBoundaryError


class ResourceView:
    def __init__(self,environment,index):
        self.environment=environment
        self.entries=dict(index['entries']);self.bodies={}
        for value in tuple(self.entries.values()):
            if 'error' not in value:
                canonical={**value,'info':{**value['info'],'is_link':False}}
                self.entries.setdefault(value['resolved'],canonical)

    def validate(self):
        # This is immutable in-memory evidence for the already checked binding;
        # actual content acquisition rechecks the opened descriptor in envd.
        if not self.environment.binding_id:raise ValueError('Missing resource binding')

    @staticmethod
    def _checked(value):
        if value.get('error')=='outside_workspace':raise ResourceBoundaryError('outside_workspace')
        if 'error' in value:raise OSError(value['error'])
        return value

    def _entry(self,path):
        return self._checked(self.entries.get(str(path),{'error':'resource_not_indexed'}))

    def resource_info(self,path):return FileResource(**self._entry(path)['info'])
    def resolve_resource(self,path):return self._entry(path)['resolved']
    def list_resources(self,path):
        value=self._entry(path)
        if 'children_error' in value:raise OSError(value['children_error'])
        return tuple(value['children'])

    def read_resource(self,path,max_bytes):
        value=self._checked(self.bodies.get(str(path),{'error':'resource_not_loaded'}))
        return base64.b64decode(value['bytes_base64'],validate=True)[:max_bytes+1]
