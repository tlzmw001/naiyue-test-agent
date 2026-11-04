"""Explicit release operations, locally or via the same fixed SSH entry."""
from pathlib import Path
import os
from uuid import UUID,uuid5
from pydantic import TypeAdapter

from naiyue_test_agent.cloud_files.configuration import read_settings
from naiyue_test_agent.cloud_files.client import CloudClient,CloudConnection
from naiyue_test_agent.cloud_install.settings import UpdateSettings,RemoteUpdateSettings
from naiyue_test_agent.cloud_install.remote import RemoteRequest,execute_remote
from naiyue_test_agent.cloud_install.worker import check_update,launch
from naiyue_test_agent.cloud_install.store import UpdateStore
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def register(commands):
    for action in ('check','update','status','resume','activate','maintenance','upgrade','cancel','discover','backup','backups','restore','restore-activate','gc'):
        p=commands.add_parser(action)
        if action in ('status','resume','cancel','restore-activate'):p.add_argument('command_id')
        elif action=='maintenance':p.add_argument('maintenance',choices=('enter','leave','status'));p.add_argument('--command-id')
        elif action in ('backup','restore','gc'):
            if action=='restore':p.add_argument('backup_id')
            p.add_argument('--command-id',required=True)
        elif action not in ('discover','backups'):
            if action=='upgrade':p.add_argument('task_id')
            p.add_argument('release_id')
            if action!='check':p.add_argument('--command-id',required=True)
            if action=='activate':p.add_argument('--expected-revision',type=int,required=True);p.add_argument('--template-id',required=True)
        if action in ('restore','restore-activate'):p.add_argument('--confirm',required=True)
        elif action=='gc':p.add_argument('--confirm')
        target=p.add_mutually_exclusive_group(required=True)
        target.add_argument('--settings',type=Path);target.add_argument('--remote',type=Path)


def execute(args):
    request=RemoteRequest.model_validate({key:getattr(args,key) for key in ('command_id','release_id','task_id','expected_revision','template_id','maintenance','backup_id','confirm') if hasattr(args,key)}|dict(action=args.release_command))
    if args.remote is not None:return execute_remote(read_settings(args.remote,RemoteUpdateSettings),request)
    if os.name=='nt':raise CloudError('update_requires_remote')
    settings=read_settings(args.settings,UpdateSettings)
    if request.action=='backups':
        from naiyue_test_agent.cloud_install.backup import list_backups
        return dict(backups=list_backups(settings))
    if request.action in ('backup','restore','restore-activate','gc'):
        store=UpdateStore(settings)
        if request.action=='restore-activate':
            if request.confirm!=request.command_id:raise CloudError('restore_confirmation_required')
            with store.lock(request.command_id):
                value=store.read(request.command_id);store.require_target(value)
                if value.get('operation')!='restore':raise CloudError('command_id_conflict')
                if value['phase']=='completed':return value
                store.save(request.command_id,activate_requested=True,phase='queued')
        elif request.action=='gc':
            store.create_operation(request.command_id,'gc',{})
            if request.confirm is not None:
                with store.lock(request.command_id):
                    value=store.read(request.command_id)
                    if request.confirm!=value.get('plan_sha256'):raise CloudError('gc_confirmation_required')
                    if value['phase']=='completed':return value
                    store.save(request.command_id,approved_digest=request.confirm,phase='queued')
        else:
            if request.action=='restore' and request.confirm!=request.backup_id:raise CloudError('restore_confirmation_required')
            store.create_operation(request.command_id,request.action,dict(backup_id=request.backup_id) if request.action=='restore' else {})
        return launch(args.settings.absolute(),request.command_id)
    if request.action in ('update','upgrade','resume','status','cancel'):
        store=UpdateStore(settings)
        if request.action=='cancel':
            client=CloudClient(read_settings(settings.connection_file,CloudConnection))
            try:
                from naiyue_test_agent.cloud_install.upgrade import cancel_pending
                with store.lock(request.command_id):return cancel_pending(settings,request.command_id,store,client)
            finally:client.close()
        if request.action=='status':
            value=store.read(request.command_id)
            try:
                with store.lock(request.command_id):
                    if value['phase'] in ('queued','running'):return value|dict(phase='interrupted')
            except CloudError as error:
                if error.code!='update_busy':raise
            return value
        if request.action!='resume':store.create(request.command_id,request.release_id,task_id=request.task_id)
        return launch(args.settings.absolute(),request.command_id)
    with_client=CloudClient(read_settings(settings.connection_file,CloudConnection))
    try:
        if request.action=='discover':
            from naiyue_test_agent.cloud_install.discovery import discover
            return discover(settings,with_client,settings_file=args.settings.absolute())
        if request.action=='check':return check_update(settings,request.release_id,with_client)
        if request.action=='activate':return with_client.request('POST','/releases/activate',json=dict(
            command_id=request.command_id,release_id=request.release_id,template_id=request.template_id,expected_revision=request.expected_revision))
        if request.maintenance=='status':return with_client.request('GET','/maintenance')
        return with_client.request('POST','/maintenance',json=dict(command_id=request.command_id,action=request.maintenance))
    finally:with_client.close()
