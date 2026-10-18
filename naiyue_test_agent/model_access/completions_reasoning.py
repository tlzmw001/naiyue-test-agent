"""Explicit reasoning_content protocol for configured Chat Completions gateways."""

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage


class ReasoningChatOpenAI(ChatOpenAI):
    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        data = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(result.generations, data['choices'], strict=True):
            reasoning = choice['message'].get('reasoning_content')
            if isinstance(reasoning, str):
                generation.message.additional_kwargs['reasoning_content'] = reasoning
        return result

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        result = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        choices = chunk.get('choices', []) or chunk.get('chunk', {}).get('choices', [])
        if result and choices:
            reasoning = (choices[0].get('delta') or {}).get('reasoning_content')
            if isinstance(reasoning, str):
                result.message.additional_kwargs['reasoning_content'] = reasoning
        return result

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = self._convert_input(input_).to_messages()
        for source, target in zip(messages, payload.get('messages', []), strict=True):
            if isinstance(source, AIMessage):
                reasoning = source.additional_kwargs.get('reasoning_content')
                if isinstance(reasoning, str):
                    target['reasoning_content'] = reasoning
        return payload
