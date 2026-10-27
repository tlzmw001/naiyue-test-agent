"""User image acceptance against the installed package, with no supplier calls."""
import base64
from contextlib import asynccontextmanager
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4


async def image_scenario(root, Model):
    from PIL import Image
    from langchain_core.messages import AIMessage
    from naiyue_test_agent import application
    from naiyue_test_agent.contracts.image_inputs import ImageUpload
    from naiyue_test_agent.contracts.image_content import message_images
    from naiyue_test_agent.image_input_settings import ImageInputSettings
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    if __package__:
        from .release_application import open_smoke_host
    else:
        from release_application import open_smoke_host
    directory=root/'user-images';directory.mkdir()
    workspace=directory/'workspace';workspace.mkdir()
    settings=AppSettings(image_inputs=ImageInputSettings(max_dimension=20))
    model=Model(replies=[AIMessage(content='offline image acceptance')],profile={'image_inputs':True})
    output=BytesIO();Image.new('RGB',(80,40),(32,72,45)).save(output,format='PNG')
    original=base64.b64encode(output.getvalue()).decode()
    upload=ImageUpload(client_id='installed-image',name='shot.png',mime_type='image/png',base64=original)

    @asynccontextmanager
    async def opened():
        with patch.object(application,'load_model_catalog',lambda _:ModelCatalog('offline',{'offline':model})):
            async with open_smoke_host('unused',workspace=workspace,data_dir=directory/'data',tools=(),
                    system_prompt='',response_format=None,settings=settings,initial_active_tools=[]) as app:
                yield app
    async with opened() as app:
        sid=await app.sessions.create('offline')
        prepared=await app.runs.prepare_input(sid,'look',[upload],message_id=str(uuid4()))
        await app.runs.start(sid,[prepared])
        canonical=(await app.history.messages(sid))[0]
        metadata=message_images(canonical)[0]
        data,mime=await app.history.image(sid,canonical.id,metadata['index'],source_revision=metadata['source_revision'],conversation_revision=0)
        with Image.open(BytesIO(data)) as decoded:
            assert decoded.size==(20,10) and decoded.format=='PNG'
        assert mime=='image/png' and base64.b64encode(data).decode()!=original
        assert any(isinstance(m.content,list) and any(b.get('type')=='image' for b in m.content) for m in model.seen[0])
    async with opened() as app:
        restored,mime=await app.history.image(sid,canonical.id,metadata['index'],source_revision=metadata['source_revision'],conversation_revision=0)
        assert restored==data and len(model.seen)==1
        try:
            await app.history.image(sid,canonical.id,metadata['index'],source_revision='0'*64,conversation_revision=0)
        except ValueError as error:
            assert str(error)=='image_revision_changed'
        else:
            raise AssertionError('Stale image revision was accepted')
        await app.sessions.delete(sid)
        try:
            await app.history.image(sid,canonical.id,metadata['index'],source_revision=metadata['source_revision'],conversation_revision=0)
        except SessionPersistenceError as error:
            assert str(error)=='session_not_found'
        else:
            raise AssertionError('Deleted image remained readable')
    return {'normalized_width':20,'normalized_height':10,'canonical_reopen':'passed',
        'source_revision_validation':'passed','deleted_unavailable':'passed','provider_requests':0}
