"""A preflight reads configuration and local pixels, without calling a provider."""

import json
import httpx
import httpx2
from PIL import Image
import pytest

from scripts.probe_provider_acceptance import main
from scripts.provider_acceptance_preflight import create_image_fixture, preflight
from test_model_access import config_for_all_kinds, write_config


def test_pillow_fixture_keeps_answer_only_in_pixels_and_preserves_existing_file(tmp_path):
    fixture = create_image_fixture(tmp_path)
    assert fixture.path.name == 'vision.png' and len(fixture.expected) == 8
    assert fixture.expected not in fixture.path.name
    original = fixture.path.read_bytes()
    with Image.open(fixture.path) as image:
        image.load()
        assert image.size == (800, 240) and image.format == 'PNG' and image.info == {}
        assert len(image.getcolors(image.width * image.height)) > 2
    with pytest.raises(FileExistsError):
        create_image_fixture(tmp_path)
    assert fixture.path.read_bytes() == original


def test_valid_preflight_uses_real_loader_without_http_requests(monkeypatch, tmp_path):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError('Unexpected provider request')

    async def async_forbidden(*args, **kwargs):
        forbidden()

    for module in (httpx, httpx2):
        monkeypatch.setattr(module.Client, 'send', forbidden)
        monkeypatch.setattr(module.AsyncClient, 'send', async_forbidden)
    data = config_for_all_kinds()
    data['models']['gateway']['api'] = 'responses'
    data['models']['gateway']['profile'] = {'image_inputs': True}
    path = write_config(tmp_path, data)
    before = path.read_bytes()
    report = preflight(path, model_id='gateway')
    assert report['status'] == 'ready' and report['http_requests'] == 0
    assert report['api'] == 'responses' and report['image_inputs'] is True
    assert report['batches'] == [8, 18, 24, 10] and report['total_limit'] == 60
    assert report['fixture'] == {'status': 'ready', 'format': 'PNG', 'width': 800, 'height': 240}
    assert attempts == [] and path.read_bytes() == before
    assert not any(name in json.dumps(report) for name in ('api_key', 'base_url', 'example.invalid'))
    assert all(provider.get('api_key', 'NOT_A_VALUE') not in json.dumps(report)
               for provider in data['providers'].values())


@pytest.mark.parametrize(('change', 'reason'), [
    ('missing_api', 'api_required_or_invalid'), ('invalid_json', 'model_config_invalid'),
    ('unknown_model', 'model_not_configured'), ('missing_file', 'model_config_invalid'),
])
def test_blocked_preflight_has_fixed_reasons_and_no_private_details(tmp_path, change, reason):
    data = config_for_all_kinds()
    if change == 'missing_api':
        data['models']['gateway'].pop('api')
    path = write_config(tmp_path, data)
    if change == 'invalid_json':
        path.write_text('PRIVATE_INVALID_CONTENT')
    if change == 'missing_file':
        path = tmp_path / 'missing.json'
    result = preflight(path, model_id='missing' if change == 'unknown_model' else 'gateway')
    assert result['status'] == 'blocked' and result['reason'] == reason
    assert result['http_requests'] == 0 and result['fixture']['status'] == 'ready'
    assert 'PRIVATE_INVALID_CONTENT' not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


def test_cli_is_preflight_only_and_returns_blocked_exit_code(tmp_path, capsys):
    data = config_for_all_kinds()
    data['models']['gateway'].pop('api')
    path = write_config(tmp_path, data)
    assert main(['--config', str(path), '--model-id', 'gateway']) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'blocked' and result['http_requests'] == 0
    with pytest.raises(SystemExit) as stopped:
        main(['--live'])
    assert stopped.value.code == 2


def test_cli_default_path_uses_shared_config_location(monkeypatch, tmp_path, capsys):
    from scripts import probe_provider_acceptance
    path = write_config(tmp_path, config_for_all_kinds())
    monkeypatch.setattr(probe_provider_acceptance, 'get_models_path', lambda: path)
    assert main(['--model-id', 'gateway']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'ready'


def test_fixture_io_error_is_reported_without_private_exception_text(monkeypatch, tmp_path, caplog):
    from scripts import provider_acceptance_preflight

    def fail(root):
        raise OSError('PRIVATE_IMAGE_PATH')

    monkeypatch.setattr(provider_acceptance_preflight, 'create_image_fixture', fail)
    report = preflight(write_config(tmp_path, config_for_all_kinds()))
    assert report['status'] == 'blocked' and report['reason'] == 'image_fixture_unavailable'
    assert report['fixture'] == {'status': 'unavailable'} and report['http_requests'] == 0
    assert 'acceptance_fixture_failed' in caplog.text
    assert 'PRIVATE_IMAGE_PATH' not in caplog.text + json.dumps(report)


def test_help_exits_before_reading_configuration(monkeypatch, capsys):
    from scripts import probe_provider_acceptance

    def forbidden(*args, **kwargs):
        raise AssertionError('Help must not prepare any request')

    monkeypatch.setattr(probe_provider_acceptance, 'preflight', forbidden)
    with pytest.raises(SystemExit) as stopped:
        main(['--help'])
    assert stopped.value.code == 0 and 'no live execution mode' in capsys.readouterr().out
