"""Bounded, permission-after-entry image IO; no model protocol decisions."""

import base64
from io import BytesIO
import logging
import os

from PIL import Image, ImageOps, UnidentifiedImageError
from langchain_core.tools import ToolException
from .control import check_tool_stop
from .progress import ToolProgress


logger = logging.getLogger(__name__)
_SUPPORTED = {'PNG', 'JPEG', 'WEBP', 'GIF', 'BMP'}
IMAGE_STOPPED = 'read stopped by the user; image detection or processing is incomplete and no file content was returned.'


def _image_header(head):
    # BMP has two reserved zero WORDs. "BM..." prose is still ordinary text,
    # including when it exceeds a separately configured image-input budget.
    if head.startswith(b'BM') and head[6:10] != b'\x00\x00\x00\x00':
        return False
    return (head.startswith((b'\x89PNG', b'\xff\xd8', b'GIF87a', b'GIF89a', b'BM',
                             b'II*\x00', b'MM\x00*', b'\x00\x00\x01\x00', b'\x00\x00\x02\x00',
                             b'\x00\x00\x00\x0cjP  \r\n\x87\n', b'\xffO\xffQ'))
            or head.startswith(b'RIFF') and head[8:12] == b'WEBP'
            or head[4:8] == b'ftyp' and head[8:12] in (b'avif', b'avis', b'heic', b'heif')
            or len(head) > 2 and head[:2] in (b'P1', b'P2', b'P3', b'P4', b'P5', b'P6', b'P7') and head[2:3].isspace())


def prepare_image_blocks(image, source_format, settings, *, control=None, progress=None):
    """Finite scaling/encoding attempts; preserve transparency and first-frame semantics."""
    check_tool_stop(control, IMAGE_STOPPED)
    alpha = image.mode in ('RGBA', 'LA') or 'transparency' in image.info
    output_format = source_format if source_format in ('JPEG', 'WEBP') else 'PNG'
    mime = {'JPEG': 'image/jpeg', 'WEBP': 'image/webp', 'PNG': 'image/png'}[output_format]
    with image.convert('RGBA' if alpha else 'RGB') as frame:
        check_tool_stop(control, IMAGE_STOPPED)
        if progress is not None:
            progress.report(phase='image_resize')
        frame.thumbnail((settings.max_dimension, settings.max_dimension), Image.Resampling.LANCZOS)
        check_tool_stop(control, IMAGE_STOPPED)
        for attempt in range(4):
            check_tool_stop(control, IMAGE_STOPPED)
            if progress is not None:
                progress.report(phase='image_encode', completed=attempt, total=4)
            output = BytesIO()
            options = {'quality': 85} if output_format == 'JPEG' else {'lossless': True} if output_format == 'WEBP' else {}
            frame.save(output, format=output_format, **options)
            check_tool_stop(control, IMAGE_STOPPED)
            encoded = output.getvalue()
            if progress is not None:
                progress.report(phase='image_encode', completed=attempt + 1, total=4)
            if 4 * ((len(encoded) + 2) // 3) <= settings.max_base64_bytes:
                note = '; first frame only' if source_format == 'GIF' else ''
                blocks = [{'type': 'text', 'text': f'Image: {output_format}, {frame.width} x {frame.height}{note}'},
                    {'type': 'image', 'mime_type': mime, 'base64': base64.b64encode(encoded).decode('ascii'),
                     'extras': {'width': frame.width, 'height': frame.height, 'source_format': source_format}}]
                check_tool_stop(control, IMAGE_STOPPED)
                return blocks
            if attempt < 3 and max(frame.size) > 1:
                frame.thumbnail((max(1, frame.width // 2), max(1, frame.height // 2)), Image.Resampling.LANCZOS)
                check_tool_stop(control, IMAGE_STOPPED)
    raise ToolException('Image still exceeds the configured base64 output budget after bounded resizing. Use a smaller image.')


def read_image(source, offset, limit, *, settings, control=None, progress=None):
    """Consume Read's already-open stream; None lets it rewind and read text."""
    check_tool_stop(control, IMAGE_STOPPED)
    progress = progress or ToolProgress(None)
    head = source.read(32)
    progress.report(phase='image_detect', completed=len(head), unit='bytes')
    check_tool_stop(control, IMAGE_STOPPED)
    if not _image_header(head):
        return None
    if offset is not None or limit is not None:
        raise ToolException('Image reads do not support text pagination (offset/limit). Remove these arguments.')
    if os.fstat(source.fileno()).st_size > settings.max_input_bytes:
        raise ToolException('Image exceeds the configured input byte budget. Use a smaller image.')
    source.seek(0)
    progress.report(phase='image_read', completed=0, unit='bytes')
    encoded = source.read(settings.max_input_bytes + 1)
    progress.report(phase='image_read', completed=len(encoded), unit='bytes')
    check_tool_stop(control, IMAGE_STOPPED)
    if len(encoded) > settings.max_input_bytes:
        raise ToolException('Image exceeds the configured input byte budget. Use a smaller image.')
    return decode_image_bytes(encoded, settings=settings, control=control, progress=progress)


def decode_image_bytes(encoded, *, settings, control=None, progress=None, expected_mime=None):
    """Decode actual bounded bytes, shared by Read and user message ingress."""
    try:
        if progress is not None:
            progress.report(phase='image_decode')
        with Image.open(BytesIO(encoded)) as image:
            check_tool_stop(control, IMAGE_STOPPED)
            if image.format not in _SUPPORTED:
                raise ToolException('Unsupported image format. Use PNG, JPEG, WebP, GIF or BMP.')
            # Read tools can run concurrently. Never change global warnings filters
            # or Pillow's global MAX_IMAGE_PIXELS; enforce the stricter threshold here.
            pixel_limit = min(settings.max_pixels, Image.MAX_IMAGE_PIXELS or settings.max_pixels)
            if image.width * image.height > pixel_limit:
                raise ToolException('Image exceeds the configured decoded pixel budget or Pillow safety threshold. Use a smaller image.')
            source_format = image.format
            mime = {'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp','GIF':'image/gif','BMP':'image/bmp'}[source_format]
            if expected_mime is not None and expected_mime != mime:
                raise ToolException('Image MIME does not match decoded format.')
            image.seek(0)
            image.load()
            check_tool_stop(control, IMAGE_STOPPED)
            with ImageOps.exif_transpose(image) as oriented:
                return prepare_image_blocks(oriented, source_format, settings, control=control, progress=progress)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        logger.warning('read_image_failed reason=pixel_budget error_type=%s', type(error).__name__)
        raise ToolException('Image exceeds the safe decoded pixel budget. Use a smaller image.') from error
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as error:
        logger.warning('read_image_failed error_type=%s', type(error).__name__)
        raise ToolException('Image could not be read or decoded. Check that the file is a supported, intact image.') from error
