"""Explicit B operator commands; defaults still use the established local entry."""
import argparse
import logging
from pathlib import Path

from naiyue_test_agent.contracts.cloud_tasks import CloudError

logger = logging.getLogger(__name__)


from naiyue_test_agent.cloud_files.configuration import read_settings


def main(argv=None):
    parser = argparse.ArgumentParser(prog='nyt cloud', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve', help='run the private Coordinator on Linux')
    serve.add_argument('--settings', type=Path, required=True)
    release = commands.add_parser('release', help='trusted image preparation, outside Coordinator')
    releases = release.add_subparsers(dest='release_command', required=True)
    prepare = releases.add_parser('prepare', help='prepare from verified local release artifacts')
    prepare.add_argument('release_id')
    prepare.add_argument('--settings', type=Path, required=True)
    from .cloud_release import register as register_releases
    register_releases(releases)
    inputs=commands.add_parser('input',help='capture or upload one explicitly selected input')
    input_commands=inputs.add_subparsers(dest='input_command',required=True)
    capture=input_commands.add_parser('capture')
    capture.add_argument('--source',type=Path,required=True)
    selection=capture.add_mutually_exclusive_group(required=True)
    for flag in ('selection','plan'):selection.add_argument('--'+flag,type=Path)
    output=capture.add_mutually_exclusive_group(required=True)
    for flag in ('output','cache'):output.add_argument('--'+flag,type=Path)
    capture.add_argument('--limits',type=Path)
    capture.add_argument('--codec',choices=('gzip','tar'),default='gzip')
    select=input_commands.add_parser('select',help='freeze a Git source plan for review')
    select.add_argument('--source',type=Path,required=True);select.add_argument('--output',type=Path,required=True)
    select.add_argument('--include',action='append',default=[]);select.add_argument('--exclude',action='append',default=[])
    select.add_argument('--limits',type=Path)
    upload=input_commands.add_parser('upload')
    upload.add_argument('directory',type=Path)
    upload.add_argument('--settings',type=Path,required=True)
    upload.add_argument('--profile')
    transfer=input_commands.add_parser('transfer');actions=transfer.add_subparsers(dest='transfer_action',required=True)
    for action in ('show','cancel'):
        item=actions.add_parser(action);item.add_argument('transfer_id');item.add_argument('--settings',type=Path,required=True)
        item.add_argument('--profile')
    from .cloud_tasks import register
    register(commands)
    args = parser.parse_args(argv)
    try:
        if args.command=='release' and args.release_command!='prepare':
            import json
            from .cloud_release import execute
            value=execute(args);print(json.dumps(value,ensure_ascii=True))
            return 0 if value.get('compatible',True) else 2
        if args.command=='input':
            import json
            from .cloud_inputs import execute
            print(json.dumps(execute(args),ensure_ascii=True))
            return 0
        if args.command in ('project','task','run','control','command','history','artifact','snapshot','usage'):
            import json
            from .cloud_tasks import execute
            print(json.dumps(execute(args),ensure_ascii=True))
            return 0
        if args.command == 'serve':
            from naiyue_test_agent.application.cloud.settings import CloudSettings
            from naiyue_test_agent.service.cloud.server import serve_cloud
            return serve_cloud(read_settings(args.settings, CloudSettings), settings_path=args.settings.expanduser().absolute())
        from naiyue_test_agent.cloud_install.template import TemplateBuildSettings, prepare_template
        receipt = prepare_template(read_settings(args.settings, TemplateBuildSettings), args.release_id)
        print(receipt.model_dump_json(indent=2))
        return 0
    except CloudError as error:
        parser.error(error.code)
    except (ValueError, OSError) as error:
        logger.warning('cloud_command_failed error_type=%s', type(error).__name__)
        parser.error('cloud_command_invalid_or_unavailable')
