"""Crash only this disposable controller; verify the original remote operation."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch
from uuid import uuid4


@asynccontextmanager
async def application(directory,context,image):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    settings=AppSettings.model_validate({'storage':{'data_dir':str(directory/'data')},
        'memory':{'auto_enabled':False},'sandbox':{'context':context,'image':image,'network':{'mode':'none'}}})
    model=FakeMessagesListChatModel(responses=[AIMessage(content='unused')])
    with patch('naiyue_test_agent.application.load_model_catalog',lambda _:ModelCatalog('fake',{'fake':model})):
        async with open_service_application('unused',tools=(),system_prompt='',response_format=None,settings=settings) as app:
            yield app


def save_manifest(directory,value):
    with (directory/'manifest.json').open('w') as target:
        json.dump(value,target);target.flush();os.fsync(target.fileno())


async def start_and_exit(directory,context,image):
    from naiyue_test_agent.agent_runtime.async_utils import run_blocking
    from naiyue_test_agent.tools.settings import BashSettings
    directory.mkdir(parents=True,exist_ok=True)
    async with application(directory,context,image) as app:
        code=directory/'code';code.mkdir()
        project=await app.projects.add_local(code,trusted=True,operation_id='P')
        manifest={'project_id':project.project_id};save_manifest(directory,manifest)
        host=await app.projects.open(project.project_id);sid=await host.sessions.create('fake')
        record=await app.environments.create(project.project_id,sid)
        ident=uuid4().hex
        manifest.update(session_id=sid,binding_id=record.binding_id,operation_id=ident);save_manifest(directory,manifest)
        settings=BashSettings()
        payload=dict(name='bash',command='printf once >> count.txt; printf alive; while [ ! -f release ]; do sleep 0.1; done; printf recovered',
            timeout=None,settings=settings.model_dump(mode='json'),process_settings=None,output_limit=100000)
        metadata=dict(offset=0,saved_bytes=0,max_archive_bytes=100000,tail_limit=10000,
            settings=settings.model_dump(mode='json'),background=True,agent_path='root')
        await run_blocking(app.environments._operations.submit,record.binding_id,ident,'shell',payload,metadata)
        deadline=time.monotonic()+30
        while True:
            row=await app.environments.operation_status(record.binding_id,ident)
            if row['observed_bytes']>=5:break
            assert time.monotonic()<deadline
            await asyncio.sleep(.1)
        # Intentional process exit bypasses every application shutdown hook.
        os._exit(0)


async def recover(directory,context,image):
    from naiyue_test_agent.agent_runtime.async_utils import run_blocking
    from naiyue_test_agent.tools.settings import WriteSettings,ReadSettings
    manifest=json.loads((directory/'manifest.json').read_text())
    project=manifest['project_id']
    try:
        async with application(directory,context,image) as app:
            binding,sid,ident=manifest['binding_id'],manifest['session_id'],manifest['operation_id']
            tasks=await app.environments.tasks(binding,project_id=project,session_id=sid)
            value=await tasks.output(ident,session_id=sid)
            assert value['status']=='running' and 'alive' in value['output'],value
            files=await app.environments.files(binding,project_id=project,session_id=sid)
            subject=await run_blocking(files.prepare_target,'release','write')
            subject.update(args={'path':'release'},path_field='path',tool_name='write',operation_id=uuid4().hex)
            await run_blocking(files.write,subject,'go',settings=WriteSettings(),control=None,progress=None,read_allowed=True)
            deadline=time.monotonic()+30
            while True:
                value=await tasks.output(ident,session_id=sid)
                if value['status']=='completed' and value['output_complete']:break
                assert time.monotonic()<deadline
                await asyncio.sleep(.1)
            assert 'recovered' in value['output'] and value['archive_complete'],value
            archive=Path(value['archive_paths']['raw'])
            assert archive.read_bytes()==b'aliverecovered'
            subject=await run_blocking(files.prepare_target,'count.txt','read')
            text=await run_blocking(files.read,subject,None,None,settings=ReadSettings(),control=None,progress=None)
            assert 'once' in text and 'onceonce' not in text
            await app.environments.destroy(binding)
    finally:
        async with application(directory,context,image) as app:
            for row in await app.environments.list(project):await app.environments.destroy(row.binding_id)


def verify_crash(directory,context,image):
    result=subprocess.run([sys.executable,'-m','scripts.sandbox_crash_probe','--directory',str(directory),
        '--context',context,'--image',image],capture_output=True,text=True,timeout=180)
    if result.returncode:
        if (directory/'manifest.json').exists():
            async def cleanup():
                manifest=json.loads((directory/'manifest.json').read_text())
                async with application(directory,context,image) as app:
                    for row in await app.environments.list(manifest['project_id']):await app.environments.destroy(row.binding_id)
            asyncio.run(cleanup())
        raise RuntimeError('Disposable crash probe failed: '+result.stderr[-3000:])
    asyncio.run(recover(directory,context,image))
    return 'passed'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--context',required=True);parser.add_argument('--image',required=True)
    args=parser.parse_args()
    asyncio.run(start_and_exit(args.directory,args.context,args.image))
