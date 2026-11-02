"""Small operator surface over B's existing owner APIs, with explicit retry identity."""
import os
from pathlib import Path
from urllib.parse import quote

from pydantic import TypeAdapter

from naiyue_test_agent.cloud_files.client import CloudClient,CloudConnection
from naiyue_test_agent.contracts.cloud_json import parse_object
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CreateProject,TaskId,UUIDText
from naiyue_test_agent.contracts.cloud_inputs import CreateTask
from naiyue_test_agent.contracts.cloud_lifecycle import EnvironmentCommand
from naiyue_test_agent.contracts.cloud_runner import StartRun,TaskControl
from naiyue_test_agent.contracts.cloud_artifacts import CaptureRequest
from naiyue_test_agent.storage.files import check_store_directory,open_store_readonly
from naiyue_test_agent.cloud_files.configuration import read_settings


def register(commands):
    for name,actions in (('project',('create','list')),('task',('create','show','start','stop','refresh')),
            ('run',('send',)),('control',('stop','recover')),('command',('show',)),('artifact',('capture','show','download'))):
        group=commands.add_parser(name);sub=group.add_subparsers(dest='action',required=True)
        for action in actions:
            p=sub.add_parser(action);p.add_argument('--settings',type=Path,required=True)
            if name in ('task','run','control','artifact') and action!='create':p.add_argument('task_id')
            if name=='command':p.add_argument('command_id')
            if name=='artifact' and action in ('show','download'):p.add_argument('artifact_id')
            if action in ('create','start','stop','recover','refresh','send','capture'):p.add_argument('--request',type=Path,required=True)
            if action=='download':p.add_argument('--output',type=Path,required=True)
    p=commands.add_parser('history');p.add_argument('task_id');p.add_argument('--settings',type=Path,required=True)
    p.add_argument('--after-seq',type=int,default=0);p.add_argument('--epoch');p.add_argument('--limit',type=int)
    p.add_argument('--view',choices=('events','messages','threads','detail'),default='events')
    p.add_argument('--thread');p.add_argument('--cursor');p.add_argument('--message-id');p.add_argument('--revision',type=int)
    p.add_argument('--offset',type=int,default=0)
    p.add_argument('--field',choices=('content','reasoning','tool_calls','file_change','mcp_result'),default='content')
    for name in ('snapshot','usage'):
        p=commands.add_parser(name);p.add_argument('task_id');p.add_argument('--settings',type=Path,required=True)
        p.add_argument('--limit',type=int)
        p.add_argument('--thread' if name=='snapshot' else '--after-model')


def execute(args):
    # Validate identifiers and request content before opening the authenticated client.
    for field,model in (('task_id',TaskId),('command_id',UUIDText),('artifact_id',UUIDText)):
        if hasattr(args,field):TypeAdapter(model).validate_python(getattr(args,field))
    models={('project','create'):CreateProject,('task','create'):CreateTask,('run','send'):StartRun,
        ('artifact','capture'):CaptureRequest,**{('control',a):TaskControl for a in ('stop','recover')},**{('task',a):EnvironmentCommand for a in ('start','stop','refresh')}}
    action=getattr(args,'action',None);body=None
    if model:=models.get((args.command,action)):
        path=args.request.expanduser().absolute();check_store_directory(path.parent)
        with os.fdopen(open_store_readonly(path),'rb') as stream:raw=stream.read(1024*1024+1)
        body=model.model_validate(parse_object(raw,byte_limit=1024*1024,depth_limit=32)).model_dump(mode='json')
        if args.command=='control' and body['kind']!=action:raise CloudError('command_kind_mismatch',422)
        if args.command=='task' and action in ('start','stop','refresh'):
            if body['kind']!=('stop_environment' if action=='stop' else action):raise CloudError('command_kind_mismatch',422)
    if args.command=='history' and (args.after_seq<0 or args.limit is not None and args.limit<1):raise CloudError('invalid_request',422)
    if args.command=='history':
        from naiyue_test_agent.contracts.cloud_history import Identity
        for value in (args.thread,args.message_id):
            if value is not None:TypeAdapter(Identity).validate_python(value)
        if args.view=='detail' and (args.thread is None or args.message_id is None or args.revision is None
                or not 1<=args.revision<=9223372036854775807 or args.offset<0):raise CloudError('invalid_history_detail',422)
        if args.view!='events' and (args.after_seq or args.epoch):raise CloudError('invalid_history_page',422)
    if args.command in ('snapshot','usage'):
        if args.limit is not None and args.limit<1:raise CloudError('invalid_history_page',422)
        from naiyue_test_agent.contracts.cloud_history import Identity
        value=args.thread if args.command=='snapshot' else args.after_model
        if value is not None:TypeAdapter(Identity).validate_python(value)
    client=CloudClient(read_settings(args.settings,CloudConnection))
    try:
        if args.command=='project':return client.request('POST','/projects',json=body) if action=='create' else client.request('GET','/projects')
        if args.command=='command':return client.request('GET','/commands/'+args.command_id)
        if args.command=='task' and action=='create':return client.request('POST','/tasks',json=body)
        path='/tasks/'+args.task_id
        if args.command in ('snapshot','usage'):
            params={}
            if args.limit is not None:params['limit']=args.limit
            if args.command=='snapshot' and args.thread is not None:params['thread_id']=args.thread
            if args.command=='usage' and args.after_model is not None:params['after_model']=args.after_model
            return client.request('GET',path+'/'+args.command,params=params)
        if args.command=='history':
            if args.view!='events':
                params={}
                if args.limit is not None:params['limit']=args.limit
                if args.cursor is not None:params['cursor']=args.cursor
                if args.thread is not None:params['thread_id']=args.thread
                endpoint='/history'
                if args.view=='threads':
                    if args.thread is not None:raise CloudError('invalid_history_page',422)
                    endpoint+='/threads'
                elif args.view=='detail':
                    if args.cursor is not None:raise CloudError('invalid_history_detail',422)
                    endpoint+='/messages/'+quote(args.message_id,safe='')
                    params.update(revision=args.revision,offset=args.offset,field=args.field)
                return client.request('GET',path+endpoint,params=params)
            params={'after_seq':args.after_seq}
            if args.epoch is not None:params['epoch']=args.epoch
            if args.limit is not None:params['limit']=args.limit
            return client.request('GET',path+'/history/events',params=params)
        if args.command=='task':return client.request('GET',path) if action=='show' else client.request('POST',path+'/commands',json=body)
        if args.command=='control':return client.request('POST',path+'/control',json=body)
        if args.command=='run':return client.request('POST',path+'/runs',json=body)
        if action=='capture':return client.request('POST',path+'/artifacts',json=body)
        if action=='download':return client.download(args.task_id,args.artifact_id,args.output)
        return client.request('GET',path+'/artifacts/'+args.artifact_id)
    finally:client.close()
