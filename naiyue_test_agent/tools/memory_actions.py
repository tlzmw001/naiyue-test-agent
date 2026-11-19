"""Action-specific public envelopes; canonical MemoryInput remains the domain contract."""
from copy import deepcopy
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator
from pydantic_core import PydanticUndefined

from .memory import MemoryInput


# Eight consumers share the existing field limits, not a second set of limits.
def _action(name, action, fields, required=()):
    definitions={'action':(Literal[action],...)}
    for key in fields.split():
        field=deepcopy(MemoryInput.model_fields[key])
        if key in required:
            field.default=PydanticUndefined
            field.default_factory=None
            nonnull=tuple(t for t in get_args(field.annotation) if t is not type(None))
            if type(None) in get_args(field.annotation) and len(nonnull)==1:
                field.annotation=nonnull[0]
        if key=='scope' and action not in ('list','search'):
            field.annotation=Literal['workspace','personal']
        if key=='sources':
            field.metadata.extend(Field(min_length=1).metadata)
        definitions[key]=(field.annotation,field)
    return create_model(name,__config__=ConfigDict(strict=True,extra='forbid'),**definitions)


_FILTERS='scope status kind machine module target subject'
ListMemory=_action('ListMemory','list',_FILTERS+' limit cursor')
SearchMemory=_action('SearchMemory','search',_FILTERS+' query expansions limit',('query',))
ReadMemory=_action('ReadMemory','read',_FILTERS+' memory_id version offset include_history',('scope','memory_id'))
RecordMemory=_action('RecordMemory','record','scope proposal sources',('scope','proposal','sources'))
UpdateMemory=_action('UpdateMemory','update','scope memory_id expected_version proposal sources',
    ('scope','memory_id','expected_version','proposal','sources'))
DisableMemory=_action('DisableMemory','disable','scope memory_id expected_version sources reason',
    ('scope','memory_id','expected_version','sources','reason'))
ForgetMemory=_action('ForgetMemory','forget','scope memory_id expected_version sources reason',
    ('scope','memory_id','expected_version','sources','reason'))
RefreshMemory=_action('RefreshMemory','refresh_overview','target_session_id start_sequence end_sequence')


class MemoryToolInput(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    request: ListMemory | SearchMemory | ReadMemory | RecordMemory | UpdateMemory | DisableMemory | ForgetMemory | RefreshMemory

    @model_validator(mode='after')
    def canonical_operation(self):
        MemoryInput.model_validate(self.request.model_dump(mode='json'))
        return self


def compile_memory_tool_request(envelope):
    public=MemoryToolInput.model_validate(envelope)
    return MemoryInput.model_validate(public.request.model_dump(mode='json')).model_dump(mode='json')


def is_memory_action_spec(spec):
    return memory_action_contract(spec) is not None


def memory_action_contract(spec):
    from .memory_wiki_actions import WikiMemoryToolInput
    if spec.tool.name!='memory' or not isinstance(spec.input_schema,type):return None
    if issubclass(spec.input_schema,WikiMemoryToolInput):return 'memory_actions_v2'
    if issubclass(spec.input_schema,MemoryToolInput):return 'memory_actions_v1'
    return None
