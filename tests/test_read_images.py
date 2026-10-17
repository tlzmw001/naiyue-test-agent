"""Real image bytes must travel through the authorized Read/ToolError chain."""

from native_application_helpers import async_test

import base64
import struct
import zlib
from io import BytesIO
import random

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.tools import ReadSettings, ReadImageSettings, create_session_read_tool
from tool_feedback_helpers import invoke_tool_feedback


def png_bytes(width=2, height=1):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress((b'\x00' + b'\xff\x00\x00\x80' * width) * height)) + chunk(b'IEND', b''))


@async_test
@pytest.mark.parametrize('name', ['image.png', 'renamed.txt'])
async def test_read_detects_image_content_and_keeps_tool_id(tmp_path, name):
    (tmp_path / name).write_bytes(png_bytes())
    message = (await invoke_tool_feedback(create_session_read_tool(), {'path': name},
        call_id='image', context={'workspace_root': tmp_path}))
    assert message.status == 'success', message.content
    assert message.tool_call_id == 'image' and isinstance(message.content, list)
    block = next(b for b in message.content if b['type'] == 'image')
    assert block['mime_type'] == 'image/png'
    assert base64.b64decode(block['base64']).startswith(b'\x89PNG')


@async_test
async def test_image_budget_is_separate_from_text_page_budget(tmp_path):
    (tmp_path / 'image.png').write_bytes(png_bytes())
    message = (await invoke_tool_feedback(create_session_read_tool(settings=ReadSettings(max_output_bytes=8)),
        {'path': 'image.png'}, call_id='image', context={'workspace_root': tmp_path}))
    assert message.status == 'success' and isinstance(message.content, list)


@async_test
async def test_image_does_not_accept_text_pagination(tmp_path):
    (tmp_path / 'image.png').write_bytes(png_bytes())
    message = (await invoke_tool_feedback(create_session_read_tool(), {'path': 'image.png', 'offset': 1},
        call_id='image', context={'workspace_root': tmp_path}))
    assert message.status == 'error' and 'pagination' in message.content


def test_image_tool_history_can_be_summarized_without_pixel_bytes():
    from naiyue_test_agent.agent_runtime.context_compaction import _validate_removed_prefix, _serialize_text_history
    messages = [AIMessage(content='', tool_calls=[{'name': 'read', 'args': {}, 'id': 'image'}]),
        ToolMessage(tool_call_id='image', content=[{'type': 'text', 'text': 'image description'},
            {'type': 'image', 'mime_type': 'image/png', 'base64': 'PRIVATE_IMAGE_PIXELS'}])]
    _validate_removed_prefix(messages)
    summary = _serialize_text_history(messages)
    assert 'image description' in summary and 'image/png' in summary
    assert 'PRIVATE_IMAGE_PIXELS' not in summary


async def image_result(tmp_path, data, settings=None, name='image.bin'):
    (tmp_path / name).write_bytes(data)
    return (await invoke_tool_feedback(create_session_read_tool(settings=ReadSettings(image=settings or ReadImageSettings())),
        {'path': name}, call_id='image', context={'workspace_root': tmp_path}))


@async_test
@pytest.mark.parametrize('format', ['PNG', 'JPEG', 'WEBP', 'GIF', 'BMP'])
async def test_supported_formats_decode_to_real_images_within_dimension_budget(tmp_path, format):
    from PIL import Image
    output = BytesIO()
    Image.new('RGB', (32, 16), 'red').save(output, format=format)
    message = (await image_result(tmp_path, output.getvalue(), ReadImageSettings(max_dimension=10)))
    assert message.status == 'success', message.content
    block = next(b for b in message.content if b['type'] == 'image')
    with Image.open(BytesIO(base64.b64decode(block['base64']))) as image:
        assert image.size == (10, 5)
        assert block['mime_type'] == Image.MIME[image.format]


@async_test
async def test_alpha_palette_and_animated_first_frame_are_preserved(tmp_path):
    from PIL import Image
    output = BytesIO()
    Image.new('RGBA', (2, 1), (255, 0, 0, 80)).save(output, format='PNG')
    result = (await image_result(tmp_path, output.getvalue()))
    with Image.open(BytesIO(base64.b64decode(result.content[-1]['base64']))) as decoded:
        assert decoded.convert('RGBA').getpixel((0, 0))[3] == 80
    first, second = Image.new('P', (2, 1), 0), Image.new('P', (2, 1), 1)
    first.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
    second.putpalette(first.getpalette())
    animation = BytesIO()
    first.save(animation, format='GIF', save_all=True, append_images=[second], duration=100, loop=0)
    result = (await image_result(tmp_path, animation.getvalue()))
    assert 'first frame only' in result.content[0]['text']
    with Image.open(BytesIO(base64.b64decode(result.content[-1]['base64']))) as decoded:
        assert getattr(decoded, 'n_frames', 1) == 1
        assert decoded.convert('RGB').getpixel((0, 0)) == (255, 0, 0)


@async_test
async def test_exif_orientation_is_applied_before_dimension_feedback(tmp_path):
    from PIL import Image
    output = BytesIO()
    image = Image.new('RGB', (3, 2), 'red')
    exif = Image.Exif(); exif[274] = 6
    image.save(output, format='JPEG', exif=exif)
    result = (await image_result(tmp_path, output.getvalue()))
    assert result.status == 'success' and '2 x 3' in result.content[0]['text']
    with Image.open(BytesIO(base64.b64decode(result.content[-1]['base64']))) as decoded:
        assert decoded.size == (2, 3) and decoded.getexif().get(274) in (None, 1)


@async_test
@pytest.mark.parametrize(('settings', 'reason'), [
    (ReadImageSettings(max_input_bytes=8), 'input byte budget'),
    (ReadImageSettings(max_pixels=1), 'pixel budget'),
    (ReadImageSettings(max_base64_bytes=4), 'output budget'),
])
async def test_image_budget_failures_are_specific_error_results(tmp_path, settings, reason):
    message = (await image_result(tmp_path, png_bytes(), settings))
    assert message.status == 'error' and reason in message.content


@async_test
async def test_output_budget_resizes_in_finite_attempts_and_remains_decodable(tmp_path):
    from PIL import Image
    output = BytesIO()
    Image.frombytes('RGB', (64, 64), random.Random(7).randbytes(64 * 64 * 3)).save(output, format='PNG')
    result = (await image_result(tmp_path, output.getvalue(), ReadImageSettings(max_base64_bytes=2000)))
    assert result.status == 'success', result.content
    block = result.content[-1]
    assert len(block['base64']) <= 2000
    with Image.open(BytesIO(base64.b64decode(block['base64']))) as decoded:
        decoded.load()
        assert decoded.width < 64


@async_test
async def test_input_and_pixel_guards_run_before_image_load(tmp_path, monkeypatch):
    from PIL import Image
    real_open, loaded, opened = Image.open, [], []
    def guarded(*args, **kwargs):
        opened.append(True)
        image = real_open(*args, **kwargs)
        image.load = lambda: loaded.append(True)
        return image
    monkeypatch.setattr(Image, 'open', guarded)
    result = (await image_result(tmp_path, png_bytes(), ReadImageSettings(max_input_bytes=8)))
    assert result.status == 'error' and opened == [] and loaded == []
    result = (await image_result(tmp_path, png_bytes(), ReadImageSettings(max_pixels=1)))
    assert result.status == 'error' and opened == [True] and loaded == []


@async_test
async def test_pillow_bomb_warning_becomes_safe_observable_feedback(tmp_path, monkeypatch, caplog):
    from PIL import Image
    def bomb(*args, **kwargs):
        raise Image.DecompressionBombWarning('PRIVATE_BOMB_DETAIL')
    monkeypatch.setattr(Image, 'open', bomb)
    result = (await image_result(tmp_path, png_bytes()))
    assert result.status == 'error' and 'pixel budget' in result.content
    assert 'reason=pixel_budget' in caplog.text and 'PRIVATE_BOMB_DETAIL' not in caplog.text


@async_test
async def test_corrupt_and_unsupported_images_do_not_fall_through_as_text(tmp_path):
    from PIL import Image
    broken = (await image_result(tmp_path, b'\x89PNG\r\n\x1a\nPRIVATE_CORRUPT'))
    assert broken.status == 'error' and 'decoded' in broken.content and 'PRIVATE' not in broken.content
    output = BytesIO()
    Image.new('RGB', (2, 2)).save(output, format='TIFF')
    unsupported = (await image_result(tmp_path, output.getvalue()))
    assert unsupported.status == 'error' and 'Unsupported image format' in unsupported.content
    assert (await image_result(tmp_path, b'ordinary text', name='looks-like.png')).content == 'ordinary text'


@async_test
async def test_unapproved_image_has_no_detection_or_decoder_io(tmp_path, monkeypatch):
    from naiyue_test_agent.tools import read as module
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    outside = tmp_path / 'outside.png'; outside.write_bytes(png_bytes())
    entered = []
    monkeypatch.setattr(module, 'read_image', lambda *args, **kwargs: entered.append(True))
    message = (await invoke_tool_feedback(create_session_read_tool(), {'path': str(outside)},
        call_id='denied', context={'workspace_root': workspace}))
    assert message.status == 'error' and entered == []


@async_test
async def test_parallel_capable_decoder_does_not_modify_process_warning_filters(tmp_path, monkeypatch):
    import warnings
    from PIL import Image
    before, observed, real_open = list(warnings.filters), [], Image.open
    def inspect(*args, **kwargs):
        observed.append(warnings.filters == before)
        return real_open(*args, **kwargs)
    monkeypatch.setattr(Image, 'open', inspect)
    result = (await image_result(tmp_path, png_bytes()))
    assert result.status == 'success'
    assert observed == [True] and warnings.filters == before


@async_test
async def test_bm_text_prefix_is_not_misclassified_as_a_bitmap(tmp_path):
    message = (await image_result(tmp_path, b'BMC documentation is ordinary text\n', ReadImageSettings(max_input_bytes=8)))
    assert message.status == 'success' and message.content == 'BMC documentation is ordinary text\n'
