"""Visible history text for summaries; canonical client calls are serialized separately."""
import json
from langchain_core.messages import AIMessage
from naiyue_test_agent.model_access.messages import content_text


def summary_content_text(message):
    content = message.content
    if isinstance(message, AIMessage) and isinstance(content, list):
        visible = []
        for block in content:
            if isinstance(block, dict):
                kind = block.get('type')
                if kind in ('reasoning', 'thinking', 'redacted_thinking'):
                    continue
                if kind in ('function_call', 'tool_use'):
                    if kind == 'function_call':
                        try:
                            arguments = json.loads(block.get('arguments'))
                        except (ValueError, TypeError) as error:
                            raise ValueError('invalid_function_call_content') from error
                        call_id = block.get('call_id')
                    else:
                        arguments, call_id = block.get('input'), block.get('id')
                    if not isinstance(arguments, dict):
                        raise ValueError('invalid_tool_call_content')
                    if not any(call['id'] == call_id and call['name'] == block.get('name')
                               and call['args'] == arguments for call in message.tool_calls):
                        raise ValueError('conflicting_function_call_content')
                    continue
            visible.append(block)
        content = visible
    return content_text(content, strict=True)
