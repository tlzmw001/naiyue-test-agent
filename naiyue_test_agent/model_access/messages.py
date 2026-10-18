"""Standard content projection and request-only multimodal model adaptation."""

import json

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI


MODEL_INPUT_METADATA_KEY = 'naiyue_model_input'
_IMAGE_TYPES = {'image', 'image_url', 'input_image'}


def is_image_block(block):
    return isinstance(block, dict) and block.get('type') in _IMAGE_TYPES


def image_description(block):
    mime = block.get('mime_type')
    if not isinstance(mime, str) or len(mime) > 80 or not mime.startswith('image/'):
        mime = 'image'
    extra = block.get('extras')
    dimensions = ''
    if isinstance(extra, dict) and all(type(extra.get(key)) is int and extra[key] > 0 for key in ('width', 'height')):
        dimensions = f'; {extra["width"]} x {extra["height"]}'
    return f'[image: {mime}{dimensions}; pixels omitted from text projection]'


def content_text(content, *, strict=False, delta=False):
    """Project complete content or only the text blocks of a streaming delta."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        if strict:
            raise ValueError('unsupported_content')
        return ''
    parts = []
    for block in content:
        if isinstance(block, str):
            if not delta:
                parts.append(block)
        elif isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str):
            parts.append(block['text'])
        elif (isinstance(block, dict) and isinstance(block.get('type'), str)
              and is_image_block(block)):
            if not delta:
                parts.append('\n' + image_description(block) + '\n')
        elif strict:
            raise ValueError('unsupported_content_block')
    return ''.join(parts)


def prepare_model_messages(messages, model):
    """Copy canonical history. Only CC tool images need an extra user carrier."""
    profile = getattr(model, 'profile', None) or {}
    vision = profile.get('image_inputs') is True
    metadata = getattr(model, 'metadata', None) or {}
    api = metadata.get(MODEL_INPUT_METADATA_KEY, {}).get('api')
    if api is None and isinstance(model, ChatOpenAI):
        if model.use_responses_api is not None:
            api = 'responses' if model.use_responses_api else 'chat_completions'
        elif vision and any(is_image_block(b) for m in messages if isinstance(m.content, list) for b in m.content):
            raise ValueError('Image input requires an explicitly configured OpenAI api')
    result, pending, carrier = [], set(), []
    for original in messages:
        message = original
        if isinstance(message, AIMessage) and message.tool_calls:
            if carrier:
                raise ValueError('Image tool batch is incomplete')
            pending = {call['id'] for call in message.tool_calls}
        blocks = message.content if isinstance(message.content, list) else []
        images = [block for block in blocks if is_image_block(block)]
        if images:
            message = original.model_copy(deep=True)
            blocks = message.content
            images = [block for block in blocks if is_image_block(block)]
        if images and not vision:
            message.content = [({'type': 'text', 'text': '[Image pixels not provided: current model does not support image input.]'}
                                if is_image_block(block) else block) for block in blocks]
        elif images and api == 'chat_completions' and isinstance(message, ToolMessage):
            if message.tool_call_id not in pending:
                raise ValueError('Image tool result has no pending paired call')
            source = json.dumps(message.tool_call_id, ensure_ascii=False)
            carrier.extend([{'type': 'text', 'text': f'Image data from tool_call_id={source}:'}, *images])
            message.content = [({'type': 'text', 'text': f'[Image from tool_call_id={source} supplied after this tool batch.]'}
                                if is_image_block(block) else block) for block in blocks]
        result.append(message)
        if isinstance(message, ToolMessage):
            pending.discard(message.tool_call_id)
            if not pending and carrier:
                result.append(HumanMessage(content=carrier, additional_kwargs={'naiyue_image_carrier': True}))
                carrier = []
    if carrier:
        raise ValueError('Image tool batch is incomplete')
    return result


class ModelInputMiddleware(AgentMiddleware):
    """No state update: overflow recovery outside sees the original request."""

    @property
    def name(self):
        return 'prepare_model_input'

    def wrap_model_call(self, request, handler):
        return handler(request.override(messages=prepare_model_messages(request.messages, request.model)))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(messages=prepare_model_messages(request.messages, request.model)))
