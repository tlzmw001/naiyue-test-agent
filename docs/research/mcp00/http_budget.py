"""Public HTTPX2 transport extension: JSON bytes bounded before MCP parsing."""
from contextlib import asynccontextmanager
import httpx2
from mcp.client.streamable_http import streamable_http_client


class LimitedJSONStream(httpx2.AsyncByteStream):
    def __init__(self, inner, limit, facts):
        self.inner, self.limit, self.facts = inner, limit, facts

    async def __aiter__(self):
        count = 0
        async for chunk in self.inner:
            count += len(chunk)
            if count > self.limit:
                error = httpx2.ReadError('json_read_budget_exceeded')
                self.facts.append({'rejected_bytes': count, 'forwarded_bytes': count - len(chunk),
                                   'error': error})
                raise error
            yield chunk

    async def aclose(self):
        await self.inner.aclose()


class BudgetTransport(httpx2.AsyncBaseTransport):
    def __init__(self, limit, facts):
        self.inner = httpx2.AsyncHTTPTransport()
        self.limit, self.facts = limit, facts

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if response.headers.get('content-type', '').startswith('application/json'):
            return httpx2.Response(response.status_code, headers=response.headers,
                stream=LimitedJSONStream(response.stream, self.limit, self.facts), extensions=response.extensions)
        return response

    async def aclose(self):
        await self.inner.aclose()


def http_factory(facts):
    def create(**kwargs):
        return httpx2.AsyncClient(**kwargs, transport=BudgetTransport(6144, facts))
    return create


@asynccontextmanager
async def sdk_transport(url, facts):
    async with http_factory(facts)() as client:
        async with streamable_http_client(url, http_client=client, max_sse_event_size=1048576) as streams:
            yield streams
