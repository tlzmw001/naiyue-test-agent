"""Read local declarations and generate disposable pixels, never call a model."""

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from secrets import choice
from tempfile import TemporaryDirectory

from langchain_openai import ChatOpenAI
from PIL import Image, ImageDraw, ImageFont

from naiyue_test_agent.model_access import ModelConfigError, load_model_catalog
from naiyue_test_agent.model_access.messages import MODEL_INPUT_METADATA_KEY


logger = logging.getLogger(__name__)
BATCH_LIMITS = (8, 18, 24, 10)


@dataclass(frozen=True)
class ImageFixture:
    path: Path
    expected: str


def create_image_fixture(root):
    expected = ''.join(choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
    image = Image.new('RGB', (800, 240), 'white')
    drawing = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=64)
    box = drawing.textbbox((0, 0), expected, font=font)
    drawing.text(((800 - box[2] + box[0]) // 2, 72), expected, fill='black', font=font)
    path = Path(root) / 'vision.png'
    with path.open('xb') as output:
        image.save(output, format='PNG')
    return ImageFixture(path, expected)


def preflight(config_path, model_id=None):
    report = {'status': 'blocked', 'reason': None, 'http_requests': 0,
              'total_limit': sum(BATCH_LIMITS), 'batches': list(BATCH_LIMITS),
              'live_requests_enabled': False}
    try:
        catalog = load_model_catalog(config_path)
    except ModelConfigError as error:
        report['reason'] = ('api_required_or_invalid' if '.api must be chat_completions or responses' in str(error)
                            else 'model_config_invalid')
        logger.warning('acceptance_preflight_blocked reason=%s', report['reason'])
    else:
        identity = model_id if model_id is not None else catalog.default_model_id
        if not isinstance(identity, str) or re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', identity) is None:
            report['reason'] = 'model_label_invalid'
        elif identity not in catalog.model_ids:
            report['reason'] = 'model_not_configured'
        else:
            model = catalog.get(identity)
            if not isinstance(model, ChatOpenAI):
                report['reason'] = 'unsupported_acceptance_adapter'
            else:
                report.update(status='ready', model_id=identity,
                    api=(model.metadata or {}).get(MODEL_INPUT_METADATA_KEY, {}).get('api'),
                    image_inputs=(model.profile or {}).get('image_inputs') is True,
                    thinking=catalog.thinking_options(identity),
                    configured_sdk_max_retries=model.max_retries, acceptance_sdk_max_retries=0)
        if report['reason'] is not None:
            logger.warning('acceptance_preflight_blocked reason=%s', report['reason'])
    try:
        with TemporaryDirectory(prefix='naiyue-provider-preflight-') as directory:
            fixture = create_image_fixture(directory)
            with Image.open(fixture.path) as image:
                image.load()
                report['fixture'] = {'status': 'ready', 'format': image.format,
                                     'width': image.width, 'height': image.height}
    except Exception as error:
        logger.error('acceptance_fixture_failed error_type=%s', type(error).__name__)
        report.update(status='blocked', reason=report['reason'] or 'image_fixture_unavailable',
                      fixture={'status': 'unavailable'})
    return report
