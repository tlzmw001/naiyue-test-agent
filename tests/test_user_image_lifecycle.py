"""Normalized user pixels survive the existing archive, snapshot and replay paths."""
import asyncio
import base64
import json
from pathlib import Path
import shutil
from uuid import uuid4
from langchain_core.messages import AIMessage
from test_persistent_sessions import build
from test_write_tool import RecordingFake
from test_composer_image_inputs import upload
from naiyue_test_agent.contracts.image_inputs import ImageUpload
from naiyue_test_agent.contracts.image_content import message_images
from naiyue_test_agent.storage.backup import create_backup, validate_backup
from naiyue_test_agent.storage.backup_schema import CURRENT_SCHEMA
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.errors import SessionPersistenceError
import pytest


def test_normalized_user_image_survives_regeneration_snapshot_reopen_and_delete(monkeypatch,tmp_path):
    async def scenario():
        database=tmp_path/'images.sqlite'
        model=RecordingFake(responses=[AIMessage(content='one'),AIMessage(content='two')],profile={'image_inputs':True})
        original=upload(size=(80,40))
        async with build(monkeypatch,database,model) as app:
            sid=await app.host.sessions.create('A')
            message=await app.host.runs.prepare_input(sid,'look',[ImageUpload(**original)],message_id=str(uuid4()))
            await app.host.runs.start(sid,[message])
            saved=(await app.host.history.messages(sid))[0]
            pixels=next(b['base64'] for b in saved.content if b['type']=='image')
            metadata=message_images(saved)[0]
            body,mime=await app.host.history.image(sid,saved.id,metadata['index'],source_revision=metadata['source_revision'],conversation_revision=0)
            assert base64.b64encode(body).decode()==pixels and mime=='image/png'
            reply=app.host.sessions._persistence.storage.replies.latest(sid)
            await app.host.runs.regenerate(sid,request_id=str(uuid4()),expected_reply_id=reply['reply_id'],expected_conversation_revision=0)
            after=(await app.host.history.messages(sid))[0]
            assert next(b['base64'] for b in after.content if b['type']=='image')==pixels
            assert after.additional_kwargs['naiyue_image_origin_run_id'] != saved.additional_kwargs['naiyue_image_origin_run_id']
            metadata=message_images(after)[0]
        with ProjectStore(database) as store:
            manifest=create_backup(store.database.connection,database,target_schema=CURRENT_SCHEMA)
        validate_backup(database,manifest['backup_id'])
        snapshot=database.with_name(database.name+'.backups')/manifest['backup_id']/'database.sqlite'
        restored=tmp_path/'snapshot-reopened.sqlite';shutil.copyfile(snapshot,restored)
        async with build(monkeypatch,restored,RecordingFake(responses=[],profile={'image_inputs':True})) as app:
            body,mime=await app.host.history.image(sid,after.id,metadata['index'],source_revision=metadata['source_revision'],conversation_revision=1)
            assert base64.b64encode(body).decode()==pixels
            await app.host.sessions.delete(sid)
            with pytest.raises(SessionPersistenceError,match='session_not_found'):
                await app.host.history.image(sid,after.id,metadata['index'],source_revision=metadata['source_revision'],conversation_revision=1)
    asyncio.run(scenario())
