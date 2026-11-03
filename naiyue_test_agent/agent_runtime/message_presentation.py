"""Pure public message projection shared by local and cloud history."""
from langchain_core.messages import AIMessage, ToolMessage
from naiyue_test_agent.model_access.messages import content_text
import logging


def material_refs_view(refs):
    """Display saved reference labels; this does not assert cache availability."""
    from uuid import UUID
    result=[]
    for ref in refs[:128] if isinstance(refs,list) else []:
        try:
            identity=str(UUID(ref['material_id']))
            if (ref['role'] not in ('manifest','patch','artifact') or not isinstance(ref['title'],str)
                    or type(ref['size']) is not int or ref['size']<0):raise ValueError('invalid_reference')
            result.append(dict(material_id=identity,title=ref['title'][:96],role=ref['role'],size=ref['size']))
        except (KeyError,TypeError,ValueError,AttributeError):
            logging.getLogger(__name__).warning('Invalid saved cloud material reference omitted')
    return result


def message_content_id(row):
    """Identify public content before preview truncation, excluding hidden SDK metadata."""
    from naiyue_test_agent.contracts.cloud_events import digest
    value={key:row.get(key) for key in ('id','type','name','status','tool_call_id','file_change','mcp_result')}
    value.update(content=content_text(row.get('content','')),reasoning=row.get('reasoning') or '',
        is_context_summary=bool(row.get('is_context_summary')),
        tool_calls=[{k:call.get(k) for k in ('id','name','args')} for call in row.get('tool_calls',[])])
    if row.get('material_refs'):value['material_refs']=row['material_refs']
    return digest(value)


def message_view(message):
    summary = bool(message.additional_kwargs.get('naiyue_compaction_summary'))
    row = {'id': message.id, 'type': message.type, 'content': content_text(message.content),
            'is_context_summary': summary, 'reply_id': message.additional_kwargs.get('naiyue_reply_id'), 'provisional': False, 'state': 'complete'}
    if message.type=='human':
        refs=material_refs_view(message.additional_kwargs.get('naiyue_cloud_material_refs'))
        if refs:row['material_refs']=refs
    if isinstance(message.content, list):
        from naiyue_test_agent.contracts.image_content import message_images
        images = message_images(message)
        if images:
            row['images'] = images
            from naiyue_test_agent.contracts.image_content import input_text
            row['content'] = input_text(message.content)
        row['content_projection'] = 'text'
    if isinstance(message, AIMessage):
        from naiyue_test_agent.model_access.reasoning import visible_reasoning
        reasoning = visible_reasoning(message)
        if reasoning:
            row['reasoning'] = reasoning
        row['tool_calls'] = message.tool_calls
    if isinstance(message, ToolMessage):
        from naiyue_test_agent.tools.file_change import change_projection
        change = change_projection(message.artifact)
        if change is not None:
            row['file_change'] = change
        from naiyue_test_agent.mcp_client.presentation import result_projection
        mcp = result_projection(message.artifact, name=message.name)
        if mcp is not None:
            row['mcp_result'] = mcp
        row.update(tool_call_id=message.tool_call_id, status=message.status, name=message.name)
    return row
