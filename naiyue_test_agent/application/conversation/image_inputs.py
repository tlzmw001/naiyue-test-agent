"""User message normalization before admission. Raw originals never persist."""
import base64
import binascii
import hashlib

from langchain_core.tools import ToolException
from ...contracts.image_inputs import ImageInputError
from ...tools.images import decode_image_bytes


def normalize_input(text, images, settings):
    if not images:
        return text
    if len(images) > settings.max_images_per_message:
        raise ImageInputError('image_input_too_large')
    if len({image.client_id for image in images}) != len(images):
        raise ImageInputError('image_input_invalid')
    blocks = [{'type':'text', 'text':text}] if text else []
    raw_total = encoded_total = 0
    for upload in images:
        if len(upload.base64) > 4*((settings.max_input_bytes+2)//3):
            raise ImageInputError('image_input_too_large')
        try:
            data = base64.b64decode(upload.base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ImageInputError('image_input_invalid') from error
        raw_total += len(data)
        if len(data) > settings.max_input_bytes or raw_total > settings.max_total_input_bytes:
            raise ImageInputError('image_input_too_large')
        try:
            normalized = decode_image_bytes(data, settings=settings, expected_mime=upload.mime_type)
        except ToolException as error:
            reason = str(error).lower()
            code = ('image_input_pixel_limit' if 'pixel budget' in reason else
                    'image_input_too_large' if 'budget' in reason else
                    'image_input_format_unsupported' if 'unsupported image' in reason else 'image_input_invalid')
            raise ImageInputError(code) from error
        normalized[0]['extras'] = {'naiyue_image_description': True}
        image = normalized[-1]
        image['extras'].update(name=upload.name, client_id=upload.client_id,
            image_digest=hashlib.sha256(base64.b64decode(image['base64'])).hexdigest())
        encoded_total += len(image['base64'])
        if encoded_total > settings.max_total_base64_bytes:
            raise ImageInputError('image_input_too_large')
        blocks.extend(normalized)
    return blocks
