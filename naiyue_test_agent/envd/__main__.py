"""An isolated Python entry point serves files or bridges one stdio request."""
import argparse
import json
import logging
import os
import socket
import socketserver
import sys
import time
from naiyue_test_agent.contracts.environment import EnvironmentRef,ResourceBoundaryError
from naiyue_test_agent.contracts.sandbox import PROTOCOL_VERSION,SOCKET_PATH,STATE_ROOT,SandboxError
from .protocol import receive,encode

logger=logging.getLogger(__name__)


class EnvdRequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        dispatcher=self.server.dispatcher
        self.registration=None
        try:self.respond()
        finally:
            if self.registration is not None:
                try:dispatcher.dispatch(dict(self.registration,operation='request_release'))
                except Exception as error:
                    logger.error('envd_search_lease_release_failed error_type=%s',type(error).__name__)

    def respond(self):
        dispatcher=self.server.dispatcher;limit=self.server.limit
        environment,session_id=dispatcher.environment,dispatcher.session_id
        request={}
        try:
            import struct
            _,uid,_=struct.unpack('3i',self.request.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid != 0:raise SandboxError('env_binding_mismatch')
            request=receive(self.rfile,limit)
            result=dispatcher.dispatch(request)
            if request.get('operation')=='request_register':self.registration=request
            response={'ok':True,'result':result}
        except Exception as error:
            logger.warning('envd_file_request_failed error_type=%s',type(error).__name__)
            from langchain_core.tools import ToolException
            from naiyue_test_agent.contracts.git_inputs import GitInputError
            code=(error.code if isinstance(error,(SandboxError,GitInputError)) else 'env_resource_outside' if isinstance(error,ResourceBoundaryError)
                else 'env_resource_unavailable' if isinstance(error,OSError) and request.get('operation') in
                ('resource_info','resolve_resource','list_resources','read_resource') else 'tool_error' if isinstance(error,ToolException)
                else 'env_operation_unknown' if request.get('operation') in ('write','edit') else 'env_protocol_mismatch')
            response={'ok':False,'error':{'code':code,
                'message':str(error)[:4096] if isinstance(error,ToolException) else type(error).__name__}}
        response.update(protocol=PROTOCOL_VERSION,request_id=request.get('request_id'),environment=environment.public(),session_id=session_id,
            generation=dispatcher.generation)
        try:
            self.wfile.write(encode(response,limit))
        except SandboxError:
            logger.error('envd_response_too_large')
            code='env_operation_unknown' if request.get('operation') in ('write','edit') else 'env_message_too_large'
            self.wfile.write(encode({**response,'ok':False,'result':None,
                'error':{'code':code,'message':'Response exceeds protocol budget'}},limit))
        if self.registration is not None:
            # This privileged UDS connection owns the ephemeral search control.
            # EOF also handles attach crashes and registration replies lost in flight.
            if self.rfile.read(1):raise SandboxError('env_protocol_mismatch')


def serve(environment,session_id,limit,*,settings=None,state_root=STATE_ROOT):
    from .operations import EnvironmentOperations
    from naiyue_test_agent.sandbox_settings import SandboxSettings
    settings=settings or SandboxSettings(max_message_bytes=limit)
    from .identity import initialize_identity
    initialize_identity(environment)
    dispatcher=EnvironmentOperations(environment,session_id,state_root,settings)
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads=False
    with dispatcher,Server(SOCKET_PATH,EnvdRequestHandler) as server:
        server.dispatcher=dispatcher;server.limit=limit
        os.chmod(SOCKET_PATH,0o600)
        server.serve_forever()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('serve','request','attach'))
    parser.add_argument('--lane',choices=('main','bulk'),default='main')
    parser.add_argument('--max-bytes',type=int,default=16777216)
    parser.add_argument('--connect-timeout',type=int,default=10)
    args=parser.parse_args()
    if not 65536<=args.max_bytes<=67108864:raise ValueError('Invalid protocol limit')
    if args.mode=='serve':
        from naiyue_test_agent.sandbox_settings import SandboxSettings
        settings=SandboxSettings.model_validate(json.loads(os.environ['NYT_SANDBOX_POLICY']))
        if settings.max_message_bytes!=args.max_bytes:raise ValueError('Protocol budget differs from recorded policy')
        serve(EnvironmentRef(**json.loads(os.environ['NYT_ENVIRONMENT'])),os.environ['NYT_SESSION_ID'],args.max_bytes,settings=settings)
    elif args.mode=='attach':
        import asyncio
        from .attach import attach
        from naiyue_test_agent.sandbox_settings import SandboxSettings
        settings=SandboxSettings.model_validate(json.loads(os.environ['NYT_SANDBOX_POLICY']))
        if settings.max_message_bytes!=args.max_bytes:raise ValueError('Protocol budget differs from recorded policy')
        asyncio.run(attach(EnvironmentRef(**json.loads(os.environ['NYT_ENVIRONMENT'])),os.environ['NYT_SESSION_ID'],
            settings,lane=args.lane))
    else:
        request=receive(sys.stdin.buffer,args.max_bytes)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as channel:
            deadline=time.monotonic()+args.connect_timeout
            while True:
                try:
                    channel.connect(SOCKET_PATH)
                    break
                except (FileNotFoundError,ConnectionRefusedError):
                    if time.monotonic()>=deadline:raise
                    time.sleep(.05)
            with channel.makefile('rwb') as stream:
                stream.write(encode(request,args.max_bytes));stream.flush()
                response=receive(stream,args.max_bytes)
        sys.stdout.buffer.write(encode(response,args.max_bytes))


if __name__=='__main__':main()
