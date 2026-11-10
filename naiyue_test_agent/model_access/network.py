"""Adapt the existing network policy to each SDK's own HTTP library."""
from urllib.parse import urlsplit

import httpx

from naiyue_test_agent.tools.web_network import selected_proxy


def client_options(url, network, *, http_module=httpx, native_proxy=None):
    if network is None or network.mode == 'auto' and not network.no_proxy:
        return {}  # Preserve SDK-specific proxies, environment handling and defaults.
    if network.mode == 'auto' and native_proxy:
        network = network.model_copy(update=dict(mode='proxy', proxy=native_proxy,
            proxy_username=None, proxy_password=None))
    proxy = selected_proxy(url, network)
    if proxy and urlsplit(proxy).scheme not in ('http', 'https'):
        raise ValueError('unsupported_model_proxy')
    # Disabling environment proxies must not disable SSL_CERT_FILE/SSL_CERT_DIR.
    verify = http_module.create_ssl_context(trust_env=True)
    if proxy and urlsplit(proxy).scheme == 'https':
        proxy = http_module.Proxy(proxy, ssl_context=verify)
    return dict(proxy=proxy, trust_env=False, verify=verify)
