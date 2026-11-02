"""Run S00 on a dedicated Linux Docker host; no Agent or business credentials."""
import argparse
import asyncio
import json
import logging
from pathlib import Path
import re
from uuid import uuid4

from scripts.cloud_probe.checks import GateError
from scripts.cloud_probe.scenario import scenario


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('domain','api-key-env','image','image-id','network','fixture-root','output'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--sdk-version',default='1.2.0')
    parser.add_argument('--server-version',default='1.2.0')
    parser.add_argument('--uid',type=int,default=1000)
    parser.add_argument('--gid',type=int,default=1000)
    parser.add_argument('--timeout',type=int,default=60)
    parser.add_argument('--ttl',type=int,default=300)
    parser.add_argument('--cpu',type=int,default=2)
    parser.add_argument('--memory',default='2Gi')
    parser.add_argument('--external-http-url')
    parser.add_argument('--external-http-sha256')
    parser.add_argument('--external-grpc-target')
    parser.add_argument('--external-expect',choices=('online','offline'),default='online')
    args=parser.parse_args()
    external=[args.external_http_url,args.external_http_sha256,args.external_grpc_target]
    if any(external) and (not all(external) or not re.fullmatch('[a-f0-9]{64}',args.external_http_sha256 or '')):
        parser.error('External relay requires HTTP URL, SHA256 and gRPC target together')
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive open is before all network/Docker actions.
    with output.open('x',encoding='utf-8') as stream:
        report={'run_id':uuid4().hex,'status':'running','phase':'starting',
            'company_acceptance':'pending','provider_requests':0}
        stream.write(json.dumps(report)+'\n');stream.flush()
        logging.basicConfig(level=logging.WARNING)
        try:
            asyncio.run(scenario(args,report))
            report['status']='local_backend_passed'
        except Exception as error:
            report['status']='failed';report['error_type']=type(error).__name__
            if isinstance(error,GateError):report['error_code']=str(error)
            logging.getLogger(__name__).error('s00_failed phase=%s error_type=%s',report['phase'],type(error).__name__)
        finally:
            stream.seek(0);stream.truncate();stream.write(json.dumps(report,indent=2)+'\n');stream.flush()
    print(json.dumps(report,indent=2))
    return 0 if report['status']=='local_backend_passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
