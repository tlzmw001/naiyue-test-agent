"""Create dedicated tasks and submit real model runs; explicit count confirmation required."""
import argparse,hashlib,json,logging,os,platform
from pathlib import Path
from uuid import uuid4
from naiyue_test_agent import __version__
from naiyue_test_agent.cloud_files.client import CloudConnection
from naiyue_test_agent.cloud_files.configuration import read_settings
from naiyue_test_agent.cloud_files.delivery_review import directory_scope
from naiyue_test_agent.cloud_install.store import directory,write
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import open_store_file,acquire_store_lock,release_store_lock
from scripts.cloud_workflow.settings import WorkflowSettings
from scripts.cloud_workflow.journal import Journal
from scripts.cloud_workflow.scenario import scenario


def run(settings,output,*,confirm_runs,resume=False):
    if confirm_runs!=settings.rounds*settings.task_count:raise CloudError('probe_confirm_runs_required')
    output=Path(output).expanduser().absolute()
    source_scope=directory_scope(settings.source);parent=output
    while not os.path.lexists(parent):parent=parent.parent
    output_scope=directory_scope(parent)
    if (output.is_relative_to(settings.source) or settings.source.is_relative_to(output)
            or source_scope[0] in output_scope or (output==parent and output_scope[0] in source_scope)):
        raise CloudError('probe_output_overlap')
    for path in (settings.connection_file,settings.selection_file):
        if path.parent.exists() and source_scope[0] in directory_scope(path.parent):raise CloudError('probe_private_file_in_source')
    if not resume:output.mkdir(mode=0o700)
    directory(output);fd=open_store_file(output/'probe.lock')
    try:
        try:acquire_store_lock(fd)
        except BlockingIOError:raise CloudError('probe_busy') from None
        fingerprint=hashlib.sha256(settings.model_dump_json().encode()).hexdigest()
        if resume and not (output/'journal.json').is_file():raise CloudError('probe_journal_required')
        journal=Journal(output/'journal.json',dict(probe_id=str(uuid4()),settings_sha256=fingerprint))
        if journal.value['settings_sha256']!=fingerprint:raise CloudError('probe_settings_changed')
        report=dict(probe_id=journal.value['probe_id'],version=__version__,platform=platform.platform(),python=platform.python_version(),
            status='running',requested_runs=confirm_runs,machine=platform.machine(),hot_environment_overhead='not_measured',
            target_2cpu_2gb='operator_must_verify',raw_ssh_throughput='not_measured',ui_acceptance='pending')
        write(output/'report.json',report)
        try:
            connection=read_settings(settings.connection_file,CloudConnection)
            report.update(scenario(settings,connection,output,journal));report['status']='passed'
        except Exception as error:
            report.update(status='failed',error_type=type(error).__name__)
            if isinstance(error,CloudError):report['error_code']=error.code
            logging.getLogger(__name__).error('workflow_failed error_type=%s',type(error).__name__)
        write(output/'report.json',report)
        return report
    finally:
        release_store_lock(fd);os.close(fd)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--confirm-runs',type=int,required=True);parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    try:
        settings=read_settings(args.settings,WorkflowSettings)
        if args.settings.parent.exists() and directory_scope(settings.source)[0] in directory_scope(args.settings.absolute().parent):
            raise CloudError('probe_private_file_in_source')
        report=run(settings,args.output,confirm_runs=args.confirm_runs,resume=args.resume)
        print(json.dumps(report,indent=2));return 0 if report['status']=='passed' else 1
    except Exception as error:
        print(json.dumps(dict(status='failed',error_type=type(error).__name__,
            error_code=error.code if isinstance(error,CloudError) else 'probe_setup_failed')));return 1


if __name__=='__main__':raise SystemExit(main())
