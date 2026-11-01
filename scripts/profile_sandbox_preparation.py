"""Measure existing sandbox preparation without changing the installed package."""
import argparse
import asyncio
from contextlib import ExitStack, contextmanager
from functools import wraps
import hashlib
import inspect
import json
import logging
from pathlib import Path
import runpy
import statistics
import sys
import tempfile
from threading import Lock
import time
from unittest.mock import patch
from uuid import uuid4

logger=logging.getLogger(__name__)
GIT_KINDS={'rev-parse','config','ls-files','check-attr','init','fetch','read-tree','fast-import',
    'update-index','write-tree','ls-tree','cat-file','update-ref','bundle','fsck','rev-list','checkout'}
RPC_KINDS={'hello','input_probe','input_begin','input_chunk','input_status','input_cache',
    'operation_submit','operation_poll','operation_status'}
STAGES={'snapshot','preflight','container_create','handshake','input_install'}


class Recorder:
    def __init__(self,limit=2000):
        self.limit=limit;self._lock=Lock();self._active=None

    def begin(self):
        with self._lock:self._active=dict(events=[],invalid=False,truncated=False,closed=False,origin=time.monotonic())

    def capture(self):
        with self._lock:return self._active

    def invalidate(self):
        with self._lock:
            if self._active is not None:self._active['invalid']=True

    def record(self,token,group,kind,seconds,encoded,failed,*,started=None):
        if token is None:return
        with self._lock:
            if token['closed']:return
            if len(token['events'])>=self.limit:
                token['invalid']=token['truncated']=True
                return
            token['events'].append((group,kind,seconds,encoded,failed,started))

    def end(self,seconds,*,ready=True):
        with self._lock:
            token=self._active;self._active=None
            if token is None:raise RuntimeError('No active measurement')
            token['closed']=True;events=list(token['events'])
        stages={};stage_counts={};details={};intervals=[];covered=0;timeline=[]
        for group,kind,duration,encoded,failed,started in events:
            if group in ('git','snapshot') and started is not None:
                relative=started-token['origin']
                timeline.append(dict(group=group,kind=kind,start_seconds=round(relative,6),
                    end_seconds=round(relative+duration,6),duration_seconds=round(duration,6),failed=bool(failed)))
            if group=='stage':
                stages[kind]=stages.get(kind,0)+duration
                stage_counts[kind]=stage_counts.get(kind,0)+1
                if started is None:covered+=duration
                else:intervals.append((started,started+duration))
            else:
                item=details.setdefault(group,{}).setdefault(kind,dict(count=0,total_seconds=0,max_seconds=0,errors=0))
                item['count']+=1;item['total_seconds']+=duration
                item['max_seconds']=max(item['max_seconds'],duration);item['errors']+=int(failed)
                if encoded:item['encoded_chars']=item.get('encoded_chars',0)+encoded
        for kinds in details.values():
            for item in kinds.values():
                for key in ('total_seconds','max_seconds'):item[key]=round(item[key],6)
        end=None
        for start,stop in sorted(intervals):
            covered+=max(0,stop-max(start,end if end is not None else start))
            end=max(stop,end if end is not None else stop)
        return dict(attempt_seconds=round(seconds,6),ready_confirmed=ready,
            ready_seconds=round(seconds,6) if ready else None,within_10_seconds=seconds<=10 if ready else None,
            stage_seconds={key:round(value,6) for key,value in stages.items()},stage_counts=stage_counts,
            stage_covered_seconds=round(covered,6),stage_overlap_seconds=round(sum(stages.values())-covered,6),
            unattributed_seconds=round(max(0,seconds-covered),6),details=details,
            timeline=sorted(timeline,key=lambda item:item['start_seconds']),
            observation_valid=not token['invalid'] and covered<=seconds+.001,event_limit_reached=token['truncated'])


def _tag(metric,args,kwargs):
    group,_,kind=metric.partition(':');encoded=0
    if group=='git':
        argv=args[1] if len(args)>1 else kwargs.get('args',[])
        kind=argv[0] if argv and argv[0] in GIT_KINDS else 'other'
    elif group=='docker':
        argv=args[2] if len(args)>2 else kwargs.get('args',[])
        pair=tuple(argv[:2])
        kind=('.'.join(pair) if pair in {('management','batch'),('container','start'),
            ('container','inspect'),('volume','inspect'),('image','inspect')} else 'info' if argv[:1]==['info'] else 'other')
    elif group=='rpc':
        kind=args[2] if len(args)>2 else kwargs.get('operation')
        if kind not in RPC_KINDS:return None
        params=args[3] if len(args)>3 else kwargs.get('params',{})
        if kind=='input_chunk':encoded=len(params['data'])
    return group,kind or 'other',encoded


@contextmanager
def observe(recorder,targets):
    from naiyue_test_agent.diagnostics import safe_log
    def decorate(original,metric):
        def finish(token,started,args,kwargs,failed):
            try:
                tag=_tag(metric,args,kwargs)
                if tag is not None:recorder.record(token,tag[0],tag[1],time.monotonic()-started,tag[2],failed,started=started)
            except Exception as error:
                recorder.invalidate()
                safe_log(logger.warning,'preparation_profile_observer_failed error_type=%s',type(error).__name__)
        if inspect.iscoroutinefunction(original):
            @wraps(original)
            async def wrapped(*args,**kwargs):
                token=recorder.capture();started=time.monotonic();failed=True
                try:
                    result=await original(*args,**kwargs);failed=False;return result
                finally:finish(token,started,args,kwargs,failed)
        else:
            @wraps(original)
            def wrapped(*args,**kwargs):
                token=recorder.capture();started=time.monotonic();failed=True
                try:
                    result=original(*args,**kwargs);failed=False;return result
                finally:finish(token,started,args,kwargs,failed)
        return wrapped
    with ExitStack() as stack:
        for target,name,metric in targets:
            stack.enter_context(patch.object(target,name,decorate(getattr(target,name),metric)))
        yield


def targets():
    from naiyue_test_agent.application.environments.inputs import InputService
    from naiyue_test_agent.execution.docker_cli import DockerCLI
    from naiyue_test_agent.execution.docker_environment import DockerEnvironment
    from naiyue_test_agent.execution.git_repository import GitRepository
    from naiyue_test_agent.execution.sandbox_transport import SandboxTransport
    from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder
    return [(InputService,'prepare','stage:snapshot'),(DockerCLI,'preflight','stage:preflight'),
        (DockerEnvironment,'create','stage:container_create'),(DockerEnvironment,'handshake','stage:handshake'),
        (InputService,'install','stage:input_install'),(InputService,'_transfer','transfer:input'),
        (GitRepository,'run','git'),(DockerCLI,'_run','docker'),
        (SandboxTransport,'_open','connection:open'),(SandboxTransport,'_request','rpc'),
        (SnapshotBuilder,'_read_local','snapshot:read_local'),(SnapshotBuilder,'_verify_local','snapshot:verify_local')]


def load_fixture(path):
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    fixture=runpy.run_path(str(path)).get('fixture')
    if not callable(fixture):raise ValueError('Fixture script must define fixture(root)')
    return fixture,digest


async def cleanup(app,project_id,report,primary):
    from naiyue_test_agent.diagnostics import safe_log
    failures=[];counts=report.setdefault('cleanup',dict(attempted=0,confirmed=0,errors=[]))
    try:
        for job in app.environments._preparations.active():
            if job['project_id']==project_id:
                try:await app.environments._preparations.stop(job['binding_id'])
                except Exception as error:failures.append(error)
    except Exception as error:failures.append(error)
    try:rows=await app.environments.list(project_id)
    except Exception as error:failures.append(error);rows=[]
    for row in rows:
        if row.lifecycle=='destroyed':continue
        try:
            if row.project_id!=project_id:raise RuntimeError('Cleanup ownership mismatch')
            counts['attempted']+=1
            await app.environments.manage(row.binding_id,'destroy',confirm_destroy=True,interrupt=True)
            if (await app.environments.get(row.binding_id)).lifecycle!='destroyed':
                raise RuntimeError('Cleanup unconfirmed')
            counts['confirmed']+=1
        except Exception as error:failures.append(error)
    for error in failures:
        counts['errors'].append(type(error).__name__)
        safe_log(logger.error,'preparation_profile_cleanup_failed error_type=%s',type(error).__name__)
    if failures and primary is None:raise failures[0]


async def scenario(directory,context,image,*,fixture,runs,report,ssh=None):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from naiyue_test_agent import __version__
    from naiyue_test_agent.agent_runtime.async_utils import await_settled
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import create_builtin_tools
    class Model(FakeMessagesListChatModel):
        def bind_tools(self,tools,**kwargs):return self
    sandbox=dict(context=context,image=image,network={'mode':'none'})
    if ssh:sandbox['ssh']=ssh
    settings=AppSettings.model_validate(dict(storage=dict(data_dir=str(directory/'data')),
        memory=dict(enabled=False,auto_enabled=False),sandbox=sandbox))
    report.update(controller_version=__version__,transport='ssh' if ssh else 'local',rounds=[],provider_requests=0,
        fixture_files=671,phase='fixture',measurement='new_session_with_existing_image_and_os_caches')
    root=directory/'source';await asyncio.to_thread(fixture,root)
    hooks=targets();report['instrumented_sources']={}
    for target,name,_ in hooks:
        file=Path(inspect.getsourcefile(getattr(target,name)))
        report['instrumented_sources'][target.__module__]=hashlib.sha256(file.read_bytes()).hexdigest()
    recorder=Recorder()
    model=Model(responses=[AIMessage(content='unused')])
    with patch('naiyue_test_agent.application.load_model_catalog',lambda _:ModelCatalog('fake',{'fake':model})),observe(recorder,hooks):
      async with open_service_application('unused',tools=create_builtin_tools(settings.tools),system_prompt='',response_format=None,settings=settings) as app:
        project=await app.projects.add_local(root,trusted=True,operation_id='profile')
        host=await app.projects.open(project.project_id)
        for index in range(runs):
            try:
                report['phase']='prepare';recorder.begin();started=time.monotonic();ready=False
                try:
                    sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                    view=await host.sessions.status(sid)
                    if view['execution']['state']!='ready':raise RuntimeError('Preparation did not reach ready')
                    ready=True
                finally:
                    sample=recorder.end(time.monotonic()-started,ready=ready);sample['round']=index+1
                    report['rounds'].append(sample)
                if (not sample['observation_valid'] or set(sample['stage_counts'])!=STAGES
                        or any(value!=1 for value in sample['stage_counts'].values())):
                    raise RuntimeError('Incomplete preparation observation')
                report['phase']='verify'
                binding=view['execution']['binding_id'];record=await app.environments.get(binding)
                saved=await app.environments.input(project.project_id,record.input_id)
                sample['bundle_bytes']=saved.snapshot.bundle_size
                sample['manifest_files']=len(saved.snapshot.manifest)
                if sample['manifest_files']!=671:raise RuntimeError('Fixture file count mismatch')
                files=await app.environments.files(binding,project_id=project.project_id,session_id=sid)
                if await files.read_resource('/workspace/AGENTS.md',4096)!=b'Follow fixture rules.\n':
                    raise RuntimeError('Fixture content mismatch')
                sample['content_verified']=True
                print(json.dumps(dict(round=index+1,ready_seconds=sample['ready_seconds'],
                    stages=sample['stage_seconds'],bundle_bytes=sample['bundle_bytes'])),flush=True)
            finally:
                primary=sys.exc_info()[1];report['phase']='cleanup' if primary is None else report['phase']
                await await_settled(cleanup(app,project.project_id,report,primary))
    values=[sample['ready_seconds'] for sample in report['rounds']]
    report['summary']=dict(count=len(values),min_seconds=min(values),median_seconds=statistics.median(values),
        max_seconds=max(values),all_within_10_seconds=all(value<=10 for value in values))
    report['phase']='completed'


def main():
    if sys.flags.optimize:raise RuntimeError('Profiling requires assertions enabled')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True);parser.add_argument('--image',required=True)
    parser.add_argument('--ssh-host');parser.add_argument('--ssh-executable',default='ssh')
    parser.add_argument('--fixture-script',required=True,type=Path)
    parser.add_argument('--runs',type=int,default=3);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if not 1<=args.runs<=5:parser.error('--runs must be between 1 and 5')
    if args.output.exists():raise FileExistsError('Choose a fresh report path')
    report=dict(schema_version=1,status='running',phase='setup',provider_requests=0)
    result=0
    try:
        fixture,digest=load_fixture(args.fixture_script);report['fixture_script_sha256']=digest
        ssh=dict(host=args.ssh_host,executable=args.ssh_executable) if args.ssh_host else None
        with tempfile.TemporaryDirectory(prefix='nyt-preparation-profile-') as directory:
            asyncio.run(scenario(Path(directory).resolve(),args.context,args.image,
                fixture=fixture,runs=args.runs,report=report,ssh=ssh))
        report['status']='measured'
    except (KeyboardInterrupt,asyncio.CancelledError) as error:
        result=130;report.update(status='cancelled',error_type=type(error).__name__)
    except Exception as error:
        result=1;report.update(status='failed',error_type=type(error).__name__)
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf-8') as output:json.dump(report,output,indent=2)
        print(json.dumps({key:report[key] for key in ('status','phase','error_type','summary','cleanup') if key in report},indent=2))
    return result


if __name__=='__main__':raise SystemExit(main())
