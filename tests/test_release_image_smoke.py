import asyncio
from test_async_application import StreamingToolModel


def test_installed_image_scenario_uses_real_application_contract(tmp_path):
    from scripts.release_image_smoke import image_scenario
    result=asyncio.run(image_scenario(tmp_path,StreamingToolModel))
    assert result=={'normalized_width':20,'normalized_height':10,'canonical_reopen':'passed',
        'source_revision_validation':'passed','deleted_unavailable':'passed','provider_requests':0}
