"""Directory validation without execution or remote schema retrieval."""

import asyncio
from types import SimpleNamespace

import pytest
from mcp.types import ListToolsResult, Tool


def tool(name='echo', **changes):
    return Tool(name=name, inputSchema=changes.pop('inputSchema', {'type': 'object'}), **changes)


def read(pages, **settings):
    from naiyue_test_agent.mcp_client.discovery import read_catalog
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    calls = []
    async def listing(*, params=None):
        calls.append(None if params is None else params.cursor)
        return pages[len(calls) - 1]
    catalog = asyncio.run(read_catalog('local', MCPServerSettings(transport='stdio', command='unused', **settings),
                                      SimpleNamespace(list_tools=listing)))
    return catalog, calls


def test_pagination_selection_original_identity_and_frozen_schema():
    pages = [ListToolsResult(tools=[tool()], nextCursor='page2'), ListToolsResult(tools=[tool('other')])]
    catalog, calls = read(pages, allowed_tools=['echo'])
    assert calls == [None, 'page2']
    assert [(t.remote_name, t.registered_name, t.selected) for t in catalog.tools] == [
        ('echo', 'mcp__local__echo', True), ('other', 'mcp__local__other', False)]
    assert len(catalog.fingerprint) == 64
    value = catalog.tools[0].input_schema
    value['type'] = 'array'
    assert catalog.tools[0].input_schema == {'type': 'object'}
    reordered, _ = read([ListToolsResult(tools=[tool('other'), tool()])])
    assert reordered.fingerprint == catalog.fingerprint
    changed, _ = read([ListToolsResult(tools=[tool('other'), tool(description='changed')])])
    assert changed.fingerprint != catalog.fingerprint


@pytest.mark.parametrize(('pages', 'settings', 'code'), [
    ([ListToolsResult(tools=[tool(), tool()])], {}, 'duplicate_tool'),
    ([ListToolsResult(tools=[tool('illegal name')])], {}, 'invalid_tool_name'),
    ([ListToolsResult(tools=[tool('a' * 41)])], {}, 'invalid_tool_name'),
    ([ListToolsResult(tools=[tool()], nextCursor='x'), ListToolsResult(tools=[], nextCursor='x')], {}, 'repeated_cursor'),
    ([ListToolsResult(tools=[], nextCursor='x'), ListToolsResult(tools=[], nextCursor='y')], {'max_tools': 1}, 'page_limit'),
    ([ListToolsResult(tools=[tool(), tool('b')])], {'max_tools': 1}, 'tool_limit'),
    ([ListToolsResult(tools=[tool()], nextCursor='x'), ListToolsResult(tools=[tool('b')])], {'max_tools': 1}, 'tool_limit'),
    ([ListToolsResult(tools=[tool()])], {'allowed_tools': ['missing']}, 'missing_allowed_tool'),
    ([ListToolsResult(tools=[tool()])], {'max_schema_bytes': 1}, 'schema_limit'),
    ([ListToolsResult(tools=[tool(inputSchema={'type': 'object', '$ref': 'https://forbidden.invalid'})])], {}, 'invalid_schema'),
    ([ListToolsResult(tools=[tool(inputSchema={'type': 'object', 'properties': {'runtime': {}}})])], {}, 'invalid_schema'),
    ([ListToolsResult(tools=[tool(outputSchema={'type': 'object', '$ref': 'https://forbidden.invalid'})])], {}, 'invalid_schema'),
])
def test_invalid_catalog_fails_closed(pages, settings, code):
    from naiyue_test_agent.mcp_client.discovery import MCPDiscoveryError
    with pytest.raises(MCPDiscoveryError, match=code):
        read(pages, **settings)


def test_empty_catalog_and_output_runtime_is_data():
    assert read([ListToolsResult(tools=[])])[0].tools == ()
    schema = {'type': 'object', 'properties': {'runtime': {'type': 'string'}}}
    catalog, _ = read([ListToolsResult(tools=[tool(outputSchema=schema)])])
    assert catalog.tools[0].output_schema == schema


def test_output_schema_local_reference_keeps_its_document_root():
    schema = {'type': 'object', '$defs': {'value': {'type': 'string'}},
              'properties': {'runtime': {'$ref': '#/$defs/value'}}}
    catalog, _ = read([ListToolsResult(tools=[tool(outputSchema=schema)])])
    assert catalog.tools[0].output_schema == schema
