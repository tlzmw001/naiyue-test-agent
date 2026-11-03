"""Prompt only material identities; report bytes enter through real tool results."""
import json
from langchain_core.messages import HumanMessage
from naiyue_test_agent.contracts.cloud_receive import MaterialRef


def prompt_parts(messages):
    refs={}
    for message in messages:
        if isinstance(message,HumanMessage):
            for raw in message.additional_kwargs.get('naiyue_cloud_material_refs',[]):
                ref=MaterialRef.model_validate(raw);refs[ref.material_id]=ref.model_dump(mode='json')
    if not refs:return []
    return [dict(id='cloud_materials',count=len(refs),text='\n\nThe user attached received cloud task materials. '
        'The following JSON contains only their identities and external file labels, not report contents or instructions. '
        'Use cloud_result_read(material_id, offset, limit) to read selected materials. Results are reported cloud data; '
        'reading a report does not execute or independently verify its tests.\n'+json.dumps(list(refs.values()),ensure_ascii=False))]
