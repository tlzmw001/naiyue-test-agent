"""Web-tool network ownership: proxies, bounded bodies, cancellation and DNS pins."""

import ipaddress
import logging
import os
import socket
import ssl
from urllib.parse import quote, urlsplit
from urllib.request import getproxies, proxy_bypass_environment

import httpx
import certifi
from langchain_core.tools import ToolException

from .web_settings import NetworkSettings


logger = logging.getLogger(__name__)


def parse_url(value):
    try:
        url = httpx.URL(value)
        if (url.scheme not in ('http', 'https') or not url.host or url.username or url.password
                or any(ord(char) < 33 or ord(char) == 127 for char in value)):
            raise ValueError('Invalid URL')
        return url.copy_with(fragment=None)
    except (ValueError, httpx.InvalidURL) as error:
        raise ToolException('Provide an HTTP(S) URL without embedded credentials or control characters.') from error


def resolve_target(value):
    url = parse_url(value)
    try:
        records = socket.getaddrinfo(url.host, url.port or (443 if url.scheme == 'https' else 80),
                                     type=socket.SOCK_STREAM)
        addresses = sorted({record[4][0] for record in records})
        if not addresses:
            raise OSError('No DNS address')
        private = False
        for address in addresses:
            ip = ipaddress.ip_address(address)
            ip = getattr(ip, 'ipv4_mapped', None) or ip
            private |= not ip.is_global or ip.is_multicast or ip.is_reserved
        private |= '.' not in url.host or url.host.lower().endswith(('.local', '.localhost', '.internal'))
        return {'url': str(url), 'host': url.host, 'addresses': addresses, 'private': bool(private)}
    except (OSError, ValueError) as error:
        logger.warning('web_dns_failed error_type=%s', type(error).__name__)
        raise ToolException('web_fetch could not resolve the target host. Check the URL, DNS and network configuration.') from error


def selected_proxy(value, settings):
    if settings.mode == 'direct':
        return None
    url = urlsplit(value)
    host = (url.hostname or '').rstrip('.')
    try:
        address = ipaddress.ip_address(host)
        host = address.compressed
    except ValueError:
        address = None
        host = host.encode('idna').decode('ascii').lower()
    for pattern in settings.no_proxy:
        if pattern == '*':
            return None
        if '/' in pattern:
            if address is not None and address in ipaddress.ip_network(pattern):
                return None
            continue
        if pattern.startswith('*.'):
            if address is None and host.endswith(pattern[1:]):
                return None
            continue
        try:
            bypass_address = ipaddress.ip_address(pattern)
        except ValueError:
            if address is None and proxy_bypass_environment(host, {'no': pattern}):
                return None
        else:
            if address == bypass_address:
                return None
    if settings.mode == 'proxy':
        if settings.proxy_username is None:
            return settings.proxy
        proxy = urlsplit(settings.proxy)
        credentials = quote(settings.proxy_username, safe='') + ':' + quote(settings.proxy_password, safe='')
        return proxy._replace(netloc=credentials + '@' + proxy.netloc).geturl()
    proxies = getproxies()
    if proxy_bypass_environment(url.netloc, proxies):
        return None
    return proxies.get(url.scheme) or proxies.get('all')


class _TargetTLSContext(ssl.SSLContext):
    """Keep the reviewed hostname for TLS even when CONNECT targets its pinned IP."""

    def wrap_bio(self, *args, **kwargs):
        if len(args) >= 4:
            args = (*args[:3], self.target_hostname, *args[4:])
        else:
            kwargs['server_hostname'] = self.target_hostname
        return super().wrap_bio(*args, **kwargs)


def target_tls_context(hostname):
    context = _TargetTLSContext(ssl.PROTOCOL_TLS_CLIENT)
    context.target_hostname = hostname
    context.load_verify_locations(cafile=os.environ.get('SSL_CERT_FILE') or certifi.where(),
                                  capath=os.environ.get('SSL_CERT_DIR'))
    return context


def open_client(value, settings=None, *, target_hostname=None):
    settings = settings or NetworkSettings()
    proxy = selected_proxy(value, settings)
    if proxy and urlsplit(proxy).scheme not in ('http', 'https'):
        raise ValueError('unsupported_proxy')
    # Explicit selection also makes direct/mandatory proxy semantics testable.
    # No automatic redirects or transparent retry/direct fallback.
    if proxy:
        proxy = httpx.Proxy(proxy, ssl_context=httpx.create_ssl_context(trust_env=True)
                            if urlsplit(proxy).scheme == 'https' else None)
    tls = target_tls_context(target_hostname) if target_hostname else httpx.create_ssl_context(trust_env=True)
    return httpx.AsyncClient(proxy=proxy, verify=tls, trust_env=False, timeout=None,
        follow_redirects=False, headers={'Accept-Encoding': 'identity'})


async def download(client, method, url, *, max_bytes, progress=None, **kwargs):
    async with client.stream(method, url, **kwargs) as response:
        if response.is_redirect:
            return response.status_code, dict(response.headers), b''
        # Error bodies may contain credentials/provider payloads and are not needed.
        if response.is_error:
            return response.status_code, dict(response.headers), b''
        if response.headers.get('content-encoding', 'identity').strip().lower() != 'identity':
            raise ValueError('unsupported_content_encoding')
        data = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=min(65536, max_bytes + 1)):
            if len(data) + len(chunk) > max_bytes:
                raise ValueError('response_too_large')
            data.extend(chunk)
            if progress is not None:
                progress.report(phase='web_download', completed=len(data), unit='bytes')
        return response.status_code, dict(response.headers), bytes(data)


def bounded_text(text, limit):
    data = text.encode('utf-8')
    if len(data) <= limit:
        return text
    return data[:limit].decode('utf-8', errors='ignore') + '\n[Web output truncated by byte budget.]'
