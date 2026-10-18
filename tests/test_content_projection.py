"""Complete messages and streaming fragments share parsing without sharing policy."""

from copy import deepcopy

import pytest

from naiyue_test_agent.model_access.messages import content_text


IMAGE = {'type': 'image', 'mime_type': 'image/png', 'extras': {'width': 8, 'height': 6},
         'base64': 'PRIVATE_PIXELS', 'url': 'https://private.example/image'}
DESCRIPTION = '[image: image/png; 8 x 6; pixels omitted from text projection]'


@pytest.mark.parametrize('content,complete,delta', [
    ('文字\n\x1b[31m', '文字\n\x1b[31m', '文字\n\x1b[31m'),
    ([], '', ''),
    (['开头', '结尾'], '开头结尾', ''),
    ([{'type': 'text', 'text': '正文'}], '正文', '正文'),
    (['开头', {'type': 'text', 'text': '正文'}, IMAGE], '开头正文\n' + DESCRIPTION + '\n', '正文'),
    ([IMAGE], '\n' + DESCRIPTION + '\n', ''),
    ([{'type': 'image_url', 'image_url': {'url': 'PRIVATE_URL'}}],
     '\n[image: image; pixels omitted from text projection]\n', ''),
    ([{'type': 'input_image', 'image_url': 'PRIVATE_URL'}],
     '\n[image: image; pixels omitted from text projection]\n', ''),
])
@pytest.mark.parametrize('strict', [False, True])
def test_complete_and_delta_preserve_their_shapes_without_mutating_content(content, complete, delta, strict):
    original = deepcopy(content)
    assert content_text(content, strict=strict) == complete
    assert content_text(content, strict=strict, delta=True) == delta
    assert content == original
    assert 'PRIVATE_' not in complete + delta and 'private.example' not in complete + delta


@pytest.mark.parametrize('block', [None, 3, {'type': 'text', 'text': 7},
    {'type': 'unknown', 'text': 'PRIVATE_UNKNOWN'}, {'type': ['text'], 'text': 'PRIVATE_UNKNOWN'},
    {'type': {'kind': 'text'}}, {'type': 'reasoning', 'reasoning': 'PRIVATE_REASONING'}])
@pytest.mark.parametrize('delta', [False, True])
def test_unknown_blocks_are_skipped_or_rejected_by_strict_contract(block, delta):
    content = [{'type': 'text', 'text': 'before'}, block, {'type': 'text', 'text': 'after'}]
    assert content_text(content, delta=delta) == 'beforeafter'
    with pytest.raises(ValueError, match='^unsupported_content_block$'):
        content_text(content, strict=True, delta=delta)


@pytest.mark.parametrize('content', [None, 7, {}, ('text',)])
@pytest.mark.parametrize('delta', [False, True])
def test_unsupported_top_level_content_has_the_same_strict_error(content, delta):
    assert content_text(content, delta=delta) == ''
    with pytest.raises(ValueError, match='^unsupported_content$'):
        content_text(content, strict=True, delta=delta)


def test_retired_projection_wrappers_are_absent():
    from naiyue_test_agent.application import cli_display
    from naiyue_test_agent.agent_runtime import async_message_observation
    from naiyue_test_agent.web import serialization
    assert not hasattr(cli_display, '_text')
    assert not hasattr(async_message_observation, '_text')
    assert not hasattr(serialization, 'text_content')
