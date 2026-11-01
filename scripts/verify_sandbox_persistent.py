"""Production persistent-channel gate against newly created disposable sandboxes."""
import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from uuid import uuid4


def distribution(values):
    ordered=sorted(values)
    return dict(count=len(values),p50_ms=round(statistics.median(values)*1000,3),
        p95_ms=round(ordered[int((len(ordered)-1)*.95)]*1000,3),max_ms=round(max(values)*1000,3))


def budget_screen(report):
    return dict(read_within_500ms=report['warm_read']['p95_ms']<=500,
        skills_within_2s=report['twenty_skills']['p95_ms']<=2000,
        offline_complete_run_within_3s=report['warm_offline_model_run_seconds']<=3)


def fixture(root):
    root.mkdir()
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'config','user.email','fixture@example.invalid'],check=True)
    subprocess.run(['git','-C',str(root),'config','user.name','fixture'],check=True)
    (root/'AGENTS.md').write_text('Follow fixture rules.\n',encoding='utf-8',newline='\n')
    for source in ('agents','claude'):
        for index in range(10):
            path=root/f'.{source}/skills/{source}-{index}/SKILL.md';path.parent.mkdir(parents=True)
            path.write_text(f'---\nname: {source}-{index}\ndescription: Fixture skill\n---\nFixture body\n',
                encoding='utf-8',newline='\n')
    # Deterministic incompressible payload: 650 data files + 20 skills + AGENTS.
    for index in range(650):
        (root/f'file-{index}.txt').write_bytes(hashlib.shake_256(str(index).encode()).digest(12000).hex().encode()[:12000])
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'commit','-qm','fixture'],check=True)


async def scenario(directory,context,image,*,ssh=None,stress=False,report=None):
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
        memory=dict(enabled=False,auto_enabled=False),sandbox=sandbox))
    root=directory/'source';await asyncio.to_thread(fixture,root)
    report=report if report is not None else {}
    report.update(transport='ssh' if ssh else 'local',provider_requests=0,fixture_files=671,
        mode='stress' if stress else 'quick',output_bytes=(200 if stress else 8)*1024*1024)
    bindings=[]
    with patch('naiyue_test_agent.application.load_model_catalog',lambda _:ModelCatalog('fake',{'fake':Model(responses=[AIMessage(content='done')])})):
      async with open_service_application('unused',tools=create_builtin_tools(settings.tools),system_prompt='',response_format=None,settings=settings) as app:
        try:
            project=await app.projects.add_local(root,trusted=True,operation_id='project')
            host=await app.projects.open(project.project_id)
            for index in range(2):
                report['phase']='cold_prepare'
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
                if value!=b'Follow fixture rules.\n':
                    report['resource_read_mismatch']=dict(expected_bytes=len(b'Follow fixture rules.\n'),
                        received_bytes=len(value),received_sha256=hashlib.sha256(value).hexdigest(),crlf_count=value.count(b'\r\n'))
                assert value==b'Follow fixture rules.\n'
                return time.monotonic()-started
            report['phase']='warm_reads'
            report['warm_resource_read']=distribution([await small(ports[0]) for _ in range(40)])
            from naiyue_test_agent.tools.settings import ReadSettings
            durations=[]
            report['phase']='skills'
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
            report['phase']='output'
            meta=dict(offset=0,saved_bytes=0,max_archive_bytes=250*1024*1024,tail_limit=4096,
                settings=BashSettings().model_dump(mode='json'),background=False)
            row=await ops.asubmit(binding,ident,'argv',dict(args=['-I','-c',
                'import sys;sys.stdout.buffer.write(b"x"*'+str(report['output_bytes'])+')'],timeout=60,
                settings=dict(executable='python',prefix_args=[]),process_settings=None,output_limit=250*1024*1024),meta)
            async def collect():
                nonlocal row
                async with asyncio.timeout(90):
                    while not ops.finished(row):
                        assert row['state']!='unknown' and not row.get('stale'),row
                        row=await ops.arefresh(binding,ident)
            output=asyncio.create_task(collect())
            try:
                async with asyncio.timeout(30):
                    while not row['observed_bytes'] and row['state'] in ('starting','running'):
                        await asyncio.sleep(.01)
                before=row['observed_bytes'];was_running=row['state']=='running'
                report['read_during_output']=distribution(await asyncio.gather(*(small(ports[i%2]) for i in range(40))))
                after=await ops.arefresh(binding,ident)
                report['output_overlap_observed']=was_running and after['observed_bytes']>before
                report['output_observed_during_read_batch']={'before':before,'after':after['observed_bytes']}
                await output
            finally:
                output.cancel();await asyncio.gather(output,return_exceptions=True)
            assert row['state']=='completed' and row['observed_bytes']==report['output_bytes'],row
            assert row['metadata']['saved_bytes']==row['observed_bytes']
            report['collected_output_bytes']=row['observed_bytes']
            # A blocked command must be stopped independently of ordinary IO.
            report['phase']='stop'
            ident=uuid4().hex
            row=await ops.asubmit(binding,ident,'argv',dict(args=['-I','-c','import time;time.sleep(60)'],
                timeout=90,settings=dict(executable='python',prefix_args=[]),process_settings=None,output_limit=None),dict(meta,max_archive_bytes=1048576))
            started=time.monotonic();row=await ops.astop(binding,ident)
            report['stop_seconds']=round(time.monotonic()-started,3);assert row['state']=='stopped',row
            report['phase']='offline_run'
            started=time.monotonic();await host.runs.start(ports[1].owner_session_id,[('user','hello')])
            report['warm_offline_model_run_seconds']=round(time.monotonic()-started,3)
            report['phase']='reconnect'
            await app.environments.manage(binding,'stop',interrupt=True)
            assert await small(ports[1])>=0
            await app.environments.start(binding)
            assert await small(ports[0])>=0
            report['other_session_survives_stop_and_reconnect']=True
        finally:
            primary=sys.exc_info()[1];failures=[]
            try:rows=await app.environments.list(project.project_id) if 'project' in locals() else []
            except Exception as error:
                failures.append(error);rows=[]
                logging.getLogger(__name__).error('persistent_gate_cleanup_failed error_type=%s',type(error).__name__)
            for row in rows:
                if row.lifecycle=='destroyed':continue
                try:await app.environments.manage(row.binding_id,'destroy',confirm_destroy=True,interrupt=True)
                except Exception as error:
                    failures.append(error)
                    logging.getLogger(__name__).error('persistent_gate_cleanup_failed error_type=%s',type(error).__name__)
            if failures and primary is None:raise failures[0]
    report['budget_screen']=budget_screen(report)
    if stress:report['budget_screen']['output_overlap_observed']=report['output_overlap_observed']
    return report


def main():
    if sys.flags.optimize:raise RuntimeError('Acceptance requires assertions enabled')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True);parser.add_argument('--image',required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--ssh-host');parser.add_argument('--ssh-executable')
    parser.add_argument('--stress',action='store_true',help='Collect 200 MiB instead of the 8 MiB quick gate')
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('Choose a fresh report path')
    ssh=dict(host=args.ssh_host,executable=args.ssh_executable or 'ssh') if args.ssh_host else None
    report=dict(status='running',phase='setup',provider_requests=0)
    try:
        with tempfile.TemporaryDirectory(prefix='nyt-persistent-gate-') as directory:
            asyncio.run(scenario(Path(directory),args.context,args.image,ssh=ssh,stress=args.stress,report=report))
        if not all(report['budget_screen'].values()):raise RuntimeError('Warm sandbox latency exceeds Spec141 budgets')
        report['status']='passed';report['phase']='completed'
    except Exception as error:
        report['status']='failed';report['error_type']=type(error).__name__
        if hasattr(error,'code'):report['error_code']=error.code
        raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,indent=2))


if __name__=='__main__':main()
