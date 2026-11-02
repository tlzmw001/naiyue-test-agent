"""Local explicit capture and upload; no environment discovery or SSH mutation."""
import os
from pydantic import TypeAdapter

from naiyue_test_agent.contracts.cloud_inputs import Selection,InputLimits
from naiyue_test_agent.cloud_files.archive import strict_json
from naiyue_test_agent.cloud_files.capture import capture
from naiyue_test_agent.cloud_files.client import CloudConnection,CloudClient
from naiyue_test_agent.storage.files import open_store_readonly,check_store_directory
from naiyue_test_agent.storage.files import open_store_file,sync_store_directory
from naiyue_test_agent.contracts.cloud_sources import SourcePlan
from naiyue_test_agent.contracts.cloud_tasks import CloudError,UUIDText
from naiyue_test_agent.cloud_files.configuration import read_settings


def execute(args):
    if args.input_command=='select':
        from naiyue_test_agent.cloud_files.sources import select_git
        limits=read_settings(args.limits,InputLimits) if args.limits else InputLimits()
        plan=select_git(args.source,limits,include=args.include,exclude=args.exclude)
        output=args.output.expanduser().absolute();check_store_directory(output.parent)
        if output.is_relative_to(args.source.absolute()):raise CloudError('input_capture_overlap')
        with os.fdopen(open_store_file(output,exclusive_create=True),'w') as stream:
            stream.write(plan.model_dump_json());stream.flush();os.fsync(stream.fileno())
        sync_store_directory(output.parent)
        return dict(plan=str(output),files=len(plan.selection['paths']),excluded=len(plan.receipt.excluded),
            deleted=len(plan.receipt.deleted),submodules=len(plan.receipt.submodules),git_commands=plan.receipt.git_commands)
    if args.input_command=='capture':
        limits=read_settings(args.limits,InputLimits) if args.limits else InputLimits()
        receipt=None
        if args.plan:
            plan=read_settings(args.plan,SourcePlan,max_bytes=limits.max_metadata_bytes*4)
            selection=Selection.model_validate(plan.selection);receipt=plan.receipt
        else:
            check_store_directory(args.selection.absolute().parent)
            with os.fdopen(open_store_readonly(args.selection),'rb') as stream:
                selection=Selection.model_validate(strict_json(stream.read(limits.max_metadata_bytes+1)))
        if args.cache:
            from naiyue_test_agent.cloud_files.cache import capture_cached
            result=capture_cached(args.source,selection,args.cache,limits,codec=args.codec,source_receipt=receipt)
        else:result=capture(args.source,selection,args.output,limits,codec=args.codec,source_receipt=receipt)
        return result.model_dump(mode='json')
    if args.input_command=='transfer':TypeAdapter(UUIDText).validate_python(args.transfer_id)
    client=CloudClient(read_settings(args.settings,CloudConnection))
    try:
        if args.input_command=='transfer':return client.request('GET' if args.transfer_action=='show' else 'DELETE','/transfers/'+args.transfer_id)
        return client.upload(args.directory)
    finally:client.close()
