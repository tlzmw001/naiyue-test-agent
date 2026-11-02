"""Loopback HTTP/protobuf-gRPC fixtures and a byte-preserving bridge relay."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import logging
import select
import socket
import socketserver
from threading import Event,Thread


class Relay:
    def __init__(self,bind_host,target,*,port=0):
        stopped=Event()
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    with socket.create_connection(target,timeout=3) as upstream:
                        self.request.settimeout(3)
                        while not stopped.is_set():
                            ready,_,_=select.select([self.request,upstream],[],[],.2)
                            for source in ready:
                                data=source.recv(65536)
                                if not data:return
                                (upstream if source is self.request else self.request).sendall(data)
                except OSError as error:
                    logging.getLogger(__name__).warning('s00_relay_io_failed error_type=%s',type(error).__name__)
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads=True
            allow_reuse_address=True
        self.server=Server((bind_host,port),Handler)
        self.stopped=stopped
        self.address=self.server.server_address
        self.thread=Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class NetworkFixture:
    def __init__(self,gateway,marker):
        # These are fixture dependencies already present in the isolated server image.
        import grpc
        from google.protobuf.wrappers_pb2 import StringValue
        body=marker.encode()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body)
            def log_message(self,*args):
                logging.getLogger(__name__).debug('s00_http_fixture_request')
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.executor=ThreadPoolExecutor(max_workers=4)
        self.grpc=grpc.server(self.executor)
        self.grpc.add_generic_rpc_handlers((grpc.method_handlers_generic_handler('s00.Echo',{
            'Unary':grpc.unary_unary_rpc_method_handler(lambda request,context:request,
                request_deserializer=StringValue.FromString,response_serializer=StringValue.SerializeToString),
            'Bidi':grpc.stream_stream_rpc_method_handler(lambda requests,context:iter(requests),
                request_deserializer=StringValue.FromString,response_serializer=StringValue.SerializeToString),
        }),))
        grpc_port=self.grpc.add_insecure_port('127.0.0.1:0')
        if not grpc_port:raise RuntimeError('s00_grpc_bind_failed')
        self.grpc.start()
        self.relays=[]
        try:
            self.relays.append(Relay(gateway,self.http.server_address))
            self.relays.append(Relay(gateway,('127.0.0.1',grpc_port)))
            self.specs=[(gateway,target,relay.address[1]) for target,relay in
                zip((self.http.server_address,('127.0.0.1',grpc_port)),self.relays)]
        except BaseException:
            self.close()
            raise

    def endpoints(self):
        http,rpc=[relay.address for relay in self.relays]
        return {'http_url':f'http://{http[0]}:{http[1]}/','grpc_target':f'{rpc[0]}:{rpc[1]}'}

    def disconnect(self):
        for relay in self.relays:relay.close()
        self.relays=[]

    def reconnect(self):
        if self.relays:raise RuntimeError('s00_relay_already_connected')
        for host,target,port in self.specs:
            self.relays.append(Relay(host,target,port=port))

    def close(self):
        self.disconnect()
        self.http.shutdown();self.http.server_close();self.thread.join()
        self.grpc.stop(0).wait()
        self.executor.shutdown(wait=True)
