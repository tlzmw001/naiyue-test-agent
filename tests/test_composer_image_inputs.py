"""User images use actual decoding and explicit, configurable budgets."""
import base64
from io import BytesIO

import pytest
from PIL import Image
from pydantic import ValidationError


def upload(*, size=(64, 32), mime='image/png', name='shot.png'):
    output = BytesIO()
    Image.new('RGB', size, (21, 35, 89)).save(output, format='PNG')
    return dict(client_id='image-one', name=name, mime_type=mime,
                base64=base64.b64encode(output.getvalue()).decode())


def test_defaults_and_nondefault_toml_reach_application(tmp_path):
    from naiyue_test_agent.image_input_settings import ImageInputSettings
    from naiyue_test_agent.settings import load_settings
    defaults = ImageInputSettings()
    assert defaults.max_dimension == 4096 and defaults.max_images_per_message == 4
    path = tmp_path / 'settings.toml'
    path.write_text('[image_inputs]\nmax_dimension=96\nmax_images_per_message=2\nmax_in_flight_requests=1\n')
    loaded = load_settings(path).image_inputs
    assert loaded.max_dimension == 96 and loaded.max_images_per_message == 2
    assert loaded.max_in_flight_requests == 1


@pytest.mark.parametrize('fields', [dict(max_dimension=True), dict(max_images_per_message=0),
    dict(max_request_bytes=1024), dict(max_input_bytes=30*1024*1024), dict(unknown=1)])
def test_invalid_or_inconsistent_settings_are_rejected(fields):
    from naiyue_test_agent.image_input_settings import ImageInputSettings
    with pytest.raises(ValidationError):
        ImageInputSettings(**fields)


def test_real_pixels_are_normalized_with_name_and_without_mutating_upload():
    from naiyue_test_agent.image_input_settings import ImageInputSettings
    from naiyue_test_agent.contracts.image_inputs import ImageUpload
    from naiyue_test_agent.application.conversation.image_inputs import normalize_input
    source = upload(size=(80, 40)); original = dict(source)
    content = normalize_input('inspect', [ImageUpload(**source)], ImageInputSettings(max_dimension=20))
    assert source == original and content[0] == {'type': 'text', 'text': 'inspect'}
    image = next(block for block in content if block['type'] == 'image')
    assert image['extras']['name'] == 'shot.png' and image['extras']['client_id'] == 'image-one'
    with Image.open(BytesIO(base64.b64decode(image['base64']))) as decoded:
        assert decoded.size == (20, 10) and decoded.format == 'PNG'


@pytest.mark.parametrize('change,code', [
    ({'base64': 'NOT_VALID_BASE64'}, 'image_input_invalid'),
    ({'mime_type': 'image/jpeg'}, 'image_input_invalid'),
    ({'base64': base64.b64encode(b'not an image').decode()}, 'image_input_invalid'),
])
def test_bad_images_are_explicit_errors(change, code):
    from naiyue_test_agent.image_input_settings import ImageInputSettings
    from naiyue_test_agent.contracts.image_inputs import ImageUpload, ImageInputError
    from naiyue_test_agent.application.conversation.image_inputs import normalize_input
    with pytest.raises(ImageInputError) as failure:
        normalize_input('', [ImageUpload(**(upload() | change))], ImageInputSettings())
    assert failure.value.code == code


def test_count_pixels_and_combined_encoded_budgets_are_enforced():
    from naiyue_test_agent.image_input_settings import ImageInputSettings
    from naiyue_test_agent.contracts.image_inputs import ImageUpload, ImageInputError
    from naiyue_test_agent.application.conversation.image_inputs import normalize_input
    images = [ImageUpload(**upload()), ImageUpload(**(upload() | {'client_id': 'two'}))]
    for settings, code in [(ImageInputSettings(max_images_per_message=1), 'image_input_too_large'),
                           (ImageInputSettings(max_pixels=10), 'image_input_pixel_limit'),
                           (ImageInputSettings(max_total_base64_bytes=1), 'image_input_too_large')]:
        with pytest.raises(ImageInputError) as failure:
            normalize_input('', images, settings)
        assert failure.value.code == code


def test_new_images_cannot_be_downgraded_but_old_images_can():
    from langchain_core.messages import HumanMessage
    from naiyue_test_agent.contracts.image_inputs import ImageInputError
    from naiyue_test_agent.model_access.messages import prepare_model_messages
    from test_model_image_messages import openai_model
    image = {'type': 'image', 'mime_type': 'image/png', 'base64': upload()['base64']}
    message = HumanMessage(content=[image], additional_kwargs={'naiyue_image_origin_run_id': 'fresh'})
    with pytest.raises(ImageInputError, match='model_image_input_unsupported'):
        prepare_model_messages([message], openai_model(vision=False), fresh_run_id='fresh')
    old = prepare_model_messages([message], openai_model(vision=False), fresh_run_id='later')
    assert 'does not support image' in old[0].content[0]['text']
    assert message.content == [image]


def test_message_image_processing_does_not_report_missing_tool_context(caplog):
    from naiyue_test_agent.image_input_settings import ImageInputSettings
    from naiyue_test_agent.contracts.image_inputs import ImageUpload
    from naiyue_test_agent.application.conversation.image_inputs import normalize_input
    normalize_input('',[ImageUpload(**upload())],ImageInputSettings())
    assert 'tool_progress_unavailable' not in caplog.text


def test_normalization_applies_orientation_and_strips_exif():
    from naiyue_test_agent.image_input_settings import ImageInputSettings
    from naiyue_test_agent.contracts.image_inputs import ImageUpload
    from naiyue_test_agent.application.conversation.image_inputs import normalize_input
    exif=Image.Exif();exif[274]=6;exif[37510]='PRIVATE_TEST_COMMENT'
    source=BytesIO();Image.new('RGB',(80,40)).save(source,format='PNG',exif=exif)
    item=upload()|{'base64':base64.b64encode(source.getvalue()).decode()}
    content=normalize_input('',[ImageUpload(**item)],ImageInputSettings())
    image=next(block for block in content if block['type']=='image')
    with Image.open(BytesIO(base64.b64decode(image['base64']))) as decoded:
        assert decoded.size==(40,80)
        assert not decoded.getexif()


def test_gif_metadata_explicitly_reports_first_frame_only():
    from langchain_core.messages import HumanMessage
    from naiyue_test_agent.image_input_settings import ImageInputSettings
    from naiyue_test_agent.contracts.image_inputs import ImageUpload
    from naiyue_test_agent.contracts.image_content import message_images
    from naiyue_test_agent.application.conversation.image_inputs import normalize_input
    source=BytesIO();Image.new('RGB',(32,16),'red').save(source,format='GIF')
    item=upload(mime='image/gif',name='clip.gif')|{'base64':base64.b64encode(source.getvalue()).decode()}
    content=normalize_input('',[ImageUpload(**item)],ImageInputSettings())
    assert message_images(HumanMessage(id='gif',content=content))[0]['first_frame_only'] is True


def test_legacy_tool_images_do_not_pay_for_user_image_source_hash(monkeypatch):
    from langchain_core.messages import ToolMessage
    from naiyue_test_agent.contracts import image_content
    def unexpected(message):raise AssertionError('Legacy images must not be hashed by user-input metadata')
    monkeypatch.setattr(image_content,'source_revision',unexpected)
    assert image_content.message_images(ToolMessage(content=[{'type':'image','mime_type':'image/png','base64':'legacy pixels'}],tool_call_id='read'))==[]


@pytest.mark.parametrize('budget', ['single_raw','combined_raw','single_encoded'])
def test_nondefault_byte_budgets_reach_normalization(budget):
    from naiyue_test_agent.image_input_settings import ImageInputSettings
    from naiyue_test_agent.contracts.image_inputs import ImageUpload,ImageInputError
    from naiyue_test_agent.application.conversation.image_inputs import normalize_input
    one=ImageUpload(**upload());two=ImageUpload(**(upload()|{'client_id':'two'}))
    size=len(base64.b64decode(one.base64))
    settings=ImageInputSettings(**({'max_input_bytes':size-1} if budget=='single_raw' else
        {'max_input_bytes':size,'max_total_input_bytes':size+1} if budget=='combined_raw' else {'max_base64_bytes':1}))
    with pytest.raises(ImageInputError) as caught:
        normalize_input('',[one,two] if budget=='combined_raw' else [one],settings)
    assert caught.value.code=='image_input_too_large' and caught.value.status==413
