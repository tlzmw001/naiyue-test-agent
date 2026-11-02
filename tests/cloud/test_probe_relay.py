"""A real byte relay must carry bytes and stop accepting after closure."""
import socket
import socketserver
from threading import Thread

import pytest


def test_relay_transports_binary_bytes_and_closes_listener():
    from scripts.cloud_probe.fixtures import Relay
    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            data=self.request.recv(1024)
            self.request.sendall(data)
    server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Echo)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    relay=Relay('127.0.0.1',server.server_address)
    address=relay.address
    try:
        with socket.create_connection(address,timeout=2) as client:
            client.sendall(b'\x00\xffproto\r\n')
            assert client.recv(1024)==b'\x00\xffproto\r\n'
    finally:
        relay.close();server.shutdown();server.server_close();thread.join()
    with pytest.raises(OSError):socket.create_connection(address,timeout=1)


def test_relay_can_rebind_same_entry_after_outage():
    from scripts.cloud_probe.fixtures import Relay
    class Echo(socketserver.BaseRequestHandler):
        def handle(self):self.request.sendall(self.request.recv(1024))
    server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Echo)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    first=Relay('127.0.0.1',server.server_address);address=first.address;first.close()
    try:
        second=Relay('127.0.0.1',server.server_address,port=address[1])
        try:
            assert second.address==address
            with socket.create_connection(address,timeout=2) as client:
                client.sendall(b'reconnected');assert client.recv(1024)==b'reconnected'
        finally:second.close()
    finally:
        server.shutdown();server.server_close();thread.join()
