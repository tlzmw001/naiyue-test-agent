"""JSON Schema definitions enter the same registry without weakening Pydantic."""
from copy import deepcopy
import math

import pytest
from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from naiyue_test_agent.tools import ToolRegistry, ToolSpec
from naiyue_test_agent.tools.permissions import ToolPermission, require_tool_permit


SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    '$defs': {'choice': {'oneOf': [
        {'type': 'object', 'properties': {'kind': {'const': 'count'}, 'value': {'type': 'integer'}},
         'required': ['kind', 'value'], 'additionalProperties': False},
        {'type': 'object', 'properties': {'kind': {'const': 'label'}, 'value': {'enum': ['A', 'B']}},
         'required': ['kind', 'value'], 'additionalProperties': False}]}},
    'properties': {'choice': {'$ref': '#/$defs/choice'},
                   'note': {'type': 'string', 'default': 'annotation'},
                   'rows': {'type': 'array', 'items': {'type': 'object',
                       'properties': {'runtime': {'type': 'string'}}, 'additionalProperties': False}}},
    'required': ['choice'],
}
VALID = {'choice': {'kind': 'count', 'value': 3}}


def definition(schema=None, *, seen=None, permission=None, result_format='content', failure=None):
    schema = deepcopy(SCHEMA if schema is None else schema)
    async def execute(runtime: ToolRuntime[dict, dict], **arguments):
        permit = require_tool_permit(runtime)
        assert permit['subject']['args'] == arguments
        if seen is not None:
            seen.append({'args': arguments, 'permit': permit, 'session_id': runtime.context['session_id']})
        if failure is not None:
            raise failure
        return ('ok', {'args': arguments}) if result_format == 'content_and_artifact' else 'ok'
    tool = StructuredTool(name='schema_probe', description='Deterministic JSON tool', args_schema=schema,
                          coroutine=execute, response_format=result_format)
    return ToolSpec(tool, schema, execution_mode='sequential',
                    permissions=permission or ToolPermission(default='ask'))


def registered(schema=None):
    spec = definition(schema)
    registry = ToolRegistry()
    registry.register(spec)
    return registry, spec


def test_json_schema_registers_and_preserves_input_and_dump_contract():
    registry, spec = registered()
    assert registry.get('schema_probe') is spec
    for args in (VALID, {'choice': {'kind': 'label', 'value': 'B'}, 'rows': [{'runtime': 'business'}]}):
        original = deepcopy(args)
        value = spec.validate_input(args).model_dump(mode='json')
        assert value == original and args == original and 'note' not in value
    assert 'runtime' not in spec.tool.tool_call_schema['properties']


@pytest.mark.parametrize('args', [
    {}, {'choice': {'kind': 'count', 'value': '3'}}, {'choice': {'kind': 'count', 'value': True}},
    {'choice': {'kind': 'label', 'value': 'C'}}, {**VALID, 'extra': 1},
    {**VALID, 'rows': [{'unknown': 1}]}, {**VALID, 'rows': 'bad'},
    {**VALID, 'runtime': {'session_id': 'spoof'}}, {**VALID, 'config': {}}, {**VALID, 'run_manager': {}},
    {**VALID, 'note': math.nan}, {**VALID, 'note': math.inf}, {**VALID, 'rows': ()},
])
def test_invalid_arguments_do_not_coerce_or_strip(args):
    from naiyue_test_agent.tools.json_schema import JsonToolInputError
    _, spec = registered()
    with pytest.raises(JsonToolInputError):
        spec.validate_input(args)


@pytest.mark.parametrize('schema', [
    {'type': 'object', 'properties': {'x': {'type': 'unknown'}}},
    {'type': 'array'}, {'properties': {}},
    {'$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'object'},
    {'type': 'object', '$ref': 'https://invalid.example/schema'},
    {'type': 'object', '$dynamicRef': 'file:///private/schema.json'},
    {'type': 'object', '$ref': '#/$defs/missing'},
    {'type': 'object', 'properties': {'runtime': {}}},
    {'type': 'object', 'allOf': [{'properties': {'config': {}}}]},
    {'type': 'object', '$defs': {'entry': {'properties': {'run_manager': {}}}}, '$ref': '#/$defs/entry'},
    {'type': 'object', '$ref': '#'},
])
def test_bad_schema_fails_without_partial_registration(schema):
    registry = ToolRegistry()
    with pytest.raises(ValueError):
        registry.register(definition(schema))
    assert registry.names() == ()


def test_default_and_enum_payloads_are_data_not_schema():
    _, spec = registered({'type': 'object', 'properties': {
        'data': {'default': {'$ref': 'https://data.example/value'}, 'enum': [{'$schema': 'business-data'}]}}})
    assert spec.validate_input({}).model_dump(mode='json') == {}
    assert spec.validate_input({'data': {'$schema': 'business-data'}}).model_dump(mode='json') == {
        'data': {'$schema': 'business-data'}}


def test_local_recursive_reference_and_annotation_format():
    schema = {'type': 'object', '$defs': {'entry': {'type': 'object', 'properties': {
        'next': {'$ref': '#/$defs/entry'}}, 'additionalProperties': False}},
        'properties': {'head': {'$ref': '#/$defs/entry'}, 'email': {'type': 'string', 'format': 'email'}}}
    _, spec = registered(schema)
    args = {'head': {'next': {}}, 'email': 'format-is-annotation'}
    assert spec.validate_input(args).model_dump(mode='json') == args


def test_schema_snapshot_is_independent_and_later_mutation_is_rejected():
    source = deepcopy(SCHEMA)
    spec = definition(source)
    registry = ToolRegistry()
    registry.register(spec)
    source.clear()
    assert spec.validate_input(VALID).model_dump(mode='json') == VALID
    spec.tool.args_schema['required'] = []
    with pytest.raises(ValueError, match='schema.*changed|changed.*schema'):
        spec.validate_input(VALID)


def test_input_errors_never_render_argument_values():
    from naiyue_test_agent.tools.json_schema import JsonToolInputError
    _, spec = registered()
    with pytest.raises(JsonToolInputError) as caught:
        spec.validate_input({'choice': {'kind': 'count', 'value': 'PRIVATE_SECRET'}})
    assert 'PRIVATE_SECRET' not in str(caught.value)
    assert 'choice' in str(caught.value)


@pytest.mark.parametrize('kind', ['missing-kwargs', 'positional-runtime', 'both-functions'])
def test_json_execution_signature_cannot_hide_or_drop_business_arguments(kind):
    async def missing_kwargs(runtime: ToolRuntime):
        return 'unused'
    async def positional_runtime(runtime: ToolRuntime, /, **arguments):
        return 'unused'
    async def valid(runtime: ToolRuntime, **arguments):
        return 'unused'
    item = StructuredTool(name='signature_probe', description='Invalid signature fixture', args_schema=SCHEMA,
                          coroutine={'missing-kwargs': missing_kwargs, 'positional-runtime': positional_runtime,
                                     'both-functions': valid}[kind],
                          func=(lambda **kwargs: 'unused') if kind == 'both-functions' else None)
    with pytest.raises(ValueError, match='runtime|arguments|async'):
        ToolRegistry().register(ToolSpec(item, SCHEMA))


def test_json_schema_unresolvable_refs_never_open_network(monkeypatch):
    import urllib.request
    def unexpected(*args, **kwargs):
        pytest.fail('Schema validation attempted network retrieval')
    monkeypatch.setattr(urllib.request, 'urlopen', unexpected)
    for schema in ({'type': 'object', '$ref': 'https://invalid.example/schema'},
                   {'type': 'object', '$ref': '#/$defs/missing'}):
        with pytest.raises(ValueError):
            registered(schema)


def test_schema_and_arguments_must_be_acyclic_finite_json():
    from naiyue_test_agent.tools.json_schema import JsonToolInputError
    cyclic = {'type': 'object'}
    cyclic['default'] = cyclic
    with pytest.raises(ValueError):
        registered(cyclic)
    _, spec = registered({'type': 'object'})
    args = {}; args['self'] = args
    for invalid in (args, {1: 'non-string-key'}, {'value': object()}, {'value': float('-inf')}, []):
        with pytest.raises(JsonToolInputError):
            spec.validate_input(invalid)


def test_distinct_execution_schema_and_file_permission_are_rejected():
    from naiyue_test_agent.tools.permissions import FilePermission
    spec = definition()
    spec.tool.args_schema = {'type': 'object'}
    with pytest.raises(ValueError, match='schema.*match'):
        ToolRegistry().register(spec)
    with pytest.raises(ValueError, match='tool-level'):
        ToolRegistry().register(definition(permission=FilePermission()))


def test_local_anchor_with_document_id_validates_locally():
    schema = {'$schema': 'https://json-schema.org/draft/2020-12/schema',
              '$id': 'https://schema.invalid/tool', 'type': 'object',
              '$defs': {'entry': {'$anchor': 'entry', 'type': 'integer'}},
              'properties': {'value': {'$ref': '#entry'}}, 'required': ['value']}
    _, spec = registered(schema)
    assert spec.validate_input({'value': 2}).model_dump(mode='json') == {'value': 2}


def test_rule_failure_does_not_publish_or_mutate_json_spec():
    from naiyue_test_agent.tools.permission_rules import PermissionSettings
    spec = definition()
    original_public, original_execution = spec.input_schema, spec.tool.args_schema
    registry = ToolRegistry()
    settings = PermissionSettings.model_validate({'allow': [{'tool': '*', 'args': {'note': 'value'}}]})
    with pytest.raises(ValueError, match='argument permission'):
        registry.register(spec, permission_settings=settings)
    assert registry.names() == () and spec.input_schema is original_public
    assert spec.tool.args_schema is original_execution and spec._json_input is None


def test_reregistering_a_mutated_spec_cannot_replace_an_existing_contract():
    registry, spec = registered()
    spec.input_schema['required'] = []
    spec.tool.args_schema['required'] = []
    other = ToolRegistry()
    with pytest.raises(ValueError, match='schema.*changed|changed.*schema'):
        other.register(spec)
    assert other.names() == () and registry.get('schema_probe') is spec
