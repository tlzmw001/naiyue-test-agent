"""Production persistent-channel gate against newly created disposable sandboxes."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import tempfile
import time
from unittest.mock import patch
from uuid import uuid4


def distribution(values):
    ordered=sorted(values)
    return dict(count=len(values),p50_ms=round(statistics.median(values)*1000,3),
        p95_ms=round(ordered[int((len(ordered)-1)*.95)]*1000,3),max_ms=round(max(values)*1000,3))


def fixture(root):
    root.mkdir()
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'config','user.email','fixture@example.invalid'],check=True)
    subprocess.run(['git','-C',str(root),'config','user.name','fixture'],check=True)
    (root/'AGENTS.md').write_text('Follow fixture rules.\n')
    for source in ('agents','claude'):
        for index in range(10):
            path=root/f'.{source}/skills/{source}-{index}/SKILL.md';path.parent.mkdir(parents=True)
            path.write_text(f'---\nname: {source}-{index}\ndescription: Fixture skill\n---\nFixture body\n')
    # Deterministic incompressible payload: 650 data files + 20 skills + AGENTS.
    for index in range(650):
        (root/f'file-{index}.txt').write_bytes(hashlib.shake_256(str(index).encode()).digest(12000).hex().encode()[:12000])
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'commit','-qm','fixture'],check=True)


async def scenario(directory,context,image,*,ssh=None):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.application.sessions.run_resources import prepare_resources
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.tools import create_builtin_tools
    from naiyue_test_agent.tools.settings import BashSettings
    class Model(FakeMessagesListChatModel):
        def bind_tools(self,tools,**kwargs):return self
    sandbox=dict(context=context,image=image,network={'mode':'none'},process_output_bytes=256*1024*1024,total_output_bytes=512*1024*1024)
    if ssh:sandbox['ssh']=ssh
    settings=AppSettings.model_validate(dict(storage=dict(data_dir=str(directory/'data')),
        memory=dict(auto_enabled=False),sandbox=sandbox))
    root=directory/'source';await asyncio.to_thread(fixture,root)
    report=dict(transport='ssh' if ssh else 'local',provider_requests=0,fixture_files=671)
    bindings=[]
    with patch('naiyue_test_agent.application.load_model_catalog',lambda _:ModelCatalog('fake',{'fake':Model(responses=[AIMessage(content='done')])})):
      async with open_service_application('unused',tools=create_builtin_tools(settings.tools),system_prompt='',response_format=None,settings=settings) as app:
        try:
            project=await app.projects.add_local(root,trusted=True,operation_id='project')
            host=await app.projects.open(project.project_id)
            for index in range(2):
                started=time.monotonic()
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                status=await host.sessions.status(sid)
                assert status['execution']['state']=='ready',status['execution']
                bindings.append(status['execution']['binding_id'])
                report[f'cold_prepare_{index}_seconds']=round(time.monotonic()-started,3)
                print('cold preparation',index,report[f'cold_prepare_{index}_seconds'],flush=True)
            ports=[await app.environments.files(binding,project_id=project.project_id,session_id=sid)
                for binding,sid in [(r.binding_id,r.session_id) for r in await app.environments.list(project.project_id)]]
            async def small(port):
                started=time.monotonic();value=await port.read_resource('/workspace/AGENTS.md',4096)
                assert value==b'Follow fixture rules.\n'
                return time.monotonic()-started
            report['warm_resource_read']=distribution([await small(ports[0]) for _ in range(40)])
            from naiyue_test_agent.tools.settings import ReadSettings
            durations=[]
            for _ in range(40):
                started=time.monotonic()
                subject=await ports[0].prepare_target('AGENTS.md','read')
                subject.update(args={'path':'AGENTS.md'},path_field='path',tool_name='read')
                value=await ports[0].read(subject,None,None,settings=ReadSettings(),control=None,progress=None)
                assert 'Follow fixture rules.' in value
                durations.append(time.monotonic()-started)
            report['warm_read']=distribution(durations)
            report['parallel_two_sandboxes']=distribution(await asyncio.gather(*(small(ports[i%2]) for i in range(80))))
            durations=[]
            for _ in range(20):
                started=time.monotonic();resources=await prepare_resources(ports[0]);durations.append(time.monotonic()-started)
                assert sum(part.get('count',0) for part in resources['prompt_parts'])==20
            report['twenty_skills']=distribution(durations)
            print('read and skills',report['warm_read'],report['twenty_skills'],flush=True)
            ops=app.environments._operations;binding=bindings[0];ident=uuid4().hex
            meta=dict(offset=0,saved_bytes=0,max_archive_bytes=250*1024*1024,tail_limit=4096,
                settings=BashSettings().model_dump(mode='json'),background=False)
            row=await ops.asubmit(binding,ident,'argv',dict(args=['-I','-c',
                'import sys;sys.stdout.buffer.write(b"x"*(200*1024*1024))'],timeout=60,
                settings=dict(executable='python',prefix_args=[]),process_settings=None,output_limit=250*1024*1024),meta)
            async def collect():
                nonlocal row
                async with asyncio.timeout(90):
                    while not ops.finished(row):
                        assert row['state']!='unknown' and not row.get('stale'),row
                        row=await ops.arefresh(binding,ident)
            output=asyncio.create_task(collect())
            report['read_during_200mib']=distribution(await asyncio.gather(*(small(ports[i%2]) for i in range(40))))
            await output
            assert row['state']=='completed' and row['observed_bytes']==200*1024*1024,row
            assert row['metadata']['saved_bytes']==row['observed_bytes']
            report['collected_output_bytes']=row['observed_bytes']
            # A blocked command must be stopped independently of ordinary IO.
            ident=uuid4().hex
            row=await ops.asubmit(binding,ident,'argv',dict(args=['-I','-c','import time;time.sleep(60)'],
                timeout=90,settings=dict(executable='python',prefix_args=[]),process_settings=None,output_limit=None),dict(meta,max_archive_bytes=1048576))
            started=time.monotonic();row=await ops.astop(binding,ident)
            report['stop_seconds']=round(time.monotonic()-started,3);assert row['state']=='stopped',row
            started=time.monotonic();await host.runs.start(ports[1].owner_session_id,[('user','hello')])
            report['warm_offline_model_run_seconds']=round(time.monotonic()-started,3)
            await app.environments.manage(binding,'stop',interrupt=True)
            assert await small(ports[1])>=0
            await app.environments.start(binding)
            assert await small(ports[0])>=0
            report['other_session_survives_stop_and_reconnect']=True
        finally:
            for row in await app.environments.list(project.project_id) if 'project' in locals() else []:
                if row.lifecycle!='destroyed':await app.environments.manage(row.binding_id,'destroy',confirm_destroy=True,interrupt=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True);parser.add_argument('--image',required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--ssh-host');parser.add_argument('--ssh-executable')
    args=parser.parse_args()
    ssh=dict(host=args.ssh_host,executable=args.ssh_executable or 'ssh') if args.ssh_host else None
    with tempfile.TemporaryDirectory(prefix='nyt-persistent-gate-') as directory:
        report=asyncio.run(scenario(Path(directory),args.context,args.image,ssh=ssh))
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
