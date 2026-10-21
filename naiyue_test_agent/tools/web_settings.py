"""Settings for host CLI integration and bounded, credential-free Web configuration."""

import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class NetworkSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True, hide_input_in_errors=True)

    mode: Literal['auto', 'direct', 'proxy'] = 'auto'
    proxy: str | None = Field(default=None, repr=False, exclude=True)
    proxy_username: str | None = Field(default=None, repr=False, exclude=True)
    proxy_password: str | None = Field(default=None, repr=False, exclude=True)
    no_proxy: tuple[str, ...] = ()

    @field_validator('no_proxy', mode='before')
    @classmethod
    def bypass_hosts(cls, values):
        if not isinstance(values, (list, tuple)):
            raise ValueError('Proxy bypass requires a list of hosts')
        hosts = []
        for value in values:
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError('Invalid proxy bypass host')
            try:
                normalized = ipaddress.ip_address(value).compressed
            except ValueError:
                try:
                    normalized = value.lstrip('.').rstrip('.').encode('idna').decode('ascii').lower()
                except UnicodeError:
                    raise ValueError('Invalid proxy bypass host') from None
                if (value.startswith('..') or value.endswith('..') or len(normalized) > 253
                        or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                                   for part in normalized.split('.'))):
                    raise ValueError('Invalid proxy bypass host')
                if value.startswith('.'):
                    normalized = '.' + normalized
            hosts.append(normalized)
        return tuple(hosts)

    @model_validator(mode='after')
    def proxy_configuration(self):
        if self.mode == 'proxy' and not self.proxy:
            raise ValueError('Forced proxy mode requires a proxy URL')
        credentials = self.proxy_username is not None or self.proxy_password is not None
        if credentials and (not self.proxy or not self.proxy_username or self.proxy_password is None):
            raise ValueError('Proxy authentication requires a URL, username and password')
        if any(any(char in value for char in '\0\r\n')
               for value in (self.proxy_username, self.proxy_password) if value is not None):
            raise ValueError('Invalid proxy authentication')
        if self.proxy is not None:
            try:
                url = urlsplit(self.proxy)
                _ = url.port
            except ValueError:
                raise ValueError('Invalid proxy URL') from None
            if (url.scheme not in ('http', 'https') or not url.hostname
                    or any(ord(c) < 33 for c in self.proxy)):
                raise ValueError('Only HTTP(S) proxy URLs are supported')
            if credentials and (url.username is not None or url.password is not None):
                raise ValueError('Conflicting proxy authentication sources')
        return self


class SearchProviderSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    endpoint: str
    api_key_env: str = Field(min_length=1)

    @field_validator('endpoint')
    @classmethod
    def endpoint_url(cls, value):
        url = urlsplit(value)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or any(ord(c) < 33 for c in value)):
            raise ValueError('Search endpoint requires a credential-free HTTPS URL')
        _ = url.port
        return value

    @field_validator('api_key_env')
    @classmethod
    def environment_name(cls, value):
        if any(not (c.isalnum() or c == '_') for c in value):
            raise ValueError('Invalid API key environment variable name')
        return value


class WebSearchSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    provider_order: tuple[Literal['baidu', 'tavily'], ...] = ('baidu', 'tavily')
    request_timeout_seconds: float = Field(default=30, ge=0, allow_inf_nan=False)
    default_results: int = Field(default=5, gt=0, le=20)
    max_results: int = Field(default=10, gt=0, le=20)
    max_query_chars: int = Field(default=2000, gt=0)
    max_response_bytes: int = Field(default=2097152, gt=0)
    max_output_bytes: int = Field(default=65536, gt=0)
    baidu: SearchProviderSettings = Field(default_factory=lambda: SearchProviderSettings(
        endpoint='https://qianfan.baidubce.com/v2/ai_search/web_search', api_key_env='BAIDU_SEARCH_API_KEY'))
    tavily: SearchProviderSettings = Field(default_factory=lambda: SearchProviderSettings(
        endpoint='https://api.tavily.com/search', api_key_env='TAVILY_API_KEY'))

    @field_validator('provider_order', mode='before')
    @classmethod
    def ordered_providers(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode='after')
    def limits(self):
        if not self.provider_order or len(set(self.provider_order)) != len(self.provider_order):
            raise ValueError('Search providers require a unique nonempty order')
        if self.default_results > self.max_results:
            raise ValueError('Search default exceeds its maximum')
        return self


class WebFetchSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    request_timeout_seconds: float = Field(default=30, ge=0, allow_inf_nan=False)
    max_url_chars: int = Field(default=8192, gt=0)
    max_response_bytes: int = Field(default=2097152, gt=0)
    max_output_bytes: int = Field(default=65536, gt=0)
    max_redirects: int = Field(default=5, ge=0)
