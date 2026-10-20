"""Local JSON Schema contracts; no coercion, defaults, or remote retrieval."""

from copy import deepcopy
import math
import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import RootModel
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012


RESERVED_ARGUMENTS = frozenset(('runtime', 'config', 'run_manager'))
_DRAFT = 'https://json-schema.org/draft/2020-12/schema'
_ROOT = 'urn:naiyue:tool-input'


class JsonToolInputError(ValueError):
    """A safe argument error: never includes rejected values or schema bodies."""

    def __init__(self, keyword, path=()):
        parts = [str(value) if isinstance(value, int) or re.fullmatch(r'[\w.-]{1,64}', str(value))
                 else '*' for value in tuple(path)[:8]]
        location = '$' + ''.join('.' + part for part in parts)
        super().__init__(f'{location}: JSON Schema {keyword} check failed. Tool was not executed.')


def _finite_json(value, active=None):
    if type(value) in (str, int, bool, type(None)):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) not in (dict, list):
        raise ValueError('Expected finite JSON values')
    active = set() if active is None else active
    if id(value) in active:
        raise ValueError('Expected acyclic JSON values')
    active.add(id(value))
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError('Expected string JSON keys')
        items = value.values()
    else:
        items = value
    for item in items:
        _finite_json(item, active)
    active.remove(id(value))


def _no_retrieval(uri):
    raise Unresolvable(ref=uri)


def _same_instance_schemas(node, resolver, active=None):
    """Inspect applicators which keep the current instance (not child fields)."""
    if isinstance(node, bool):
        return set()
    active = set() if active is None else active
    if id(node) in active:
        raise ValueError('JSON schema contains a non-progressing reference cycle')
    active = active | {id(node)}
    fields = set(node.get('properties', {}))
    for key in ('$ref', '$dynamicRef'):
        if key in node:
            resolved = resolver.lookup(node[key])
            fields |= _same_instance_schemas(resolved.contents, resolved.resolver, active)
    children = [child for key in ('allOf', 'anyOf', 'oneOf') for child in node.get(key, [])]
    children += [node[key] for key in ('not', 'if', 'then', 'else') if key in node]
    children += list(node.get('dependentSchemas', {}).values())
    for child in children:
        resource = Resource.from_contents(child, default_specification=DRAFT202012)
        fields |= _same_instance_schemas(child, resolver.in_subresource(resource), active)
    return fields


class JsonInputContract:
    def __init__(self, schema):
        try:
            _finite_json(schema)
        except (ValueError, RecursionError):
            raise ValueError('Tool schema must contain finite, acyclic JSON') from None
        if schema.get('type') != 'object':
            raise ValueError('Tool JSON schema must declare an object root')
        self.schema = deepcopy(schema)
        try:
            Draft202012Validator.check_schema(self.schema)
        except SchemaError:
            raise ValueError('Invalid tool JSON schema') from None
        resource = Resource.from_contents(self.schema, default_specification=DRAFT202012)
        registry = Registry(retrieve=_no_retrieval).with_resource(_ROOT, resource).crawl()
        resolver = registry.resolver(_ROOT).in_subresource(resource)
        try:
            self._check_resources(resource, resolver)
            fields = _same_instance_schemas(self.schema, resolver)
        except Unresolvable:
            raise ValueError('Tool schema has an unresolved local reference') from None
        except RecursionError:
            raise ValueError('Tool schema cannot be evaluated within the local reference contract') from None
        if fields & RESERVED_ARGUMENTS:
            raise ValueError('Tool public schema contains reserved runtime/config/run_manager arguments')
        self.validator = Draft202012Validator(self.schema, registry=registry)

    def _check_resources(self, resource, resolver):
        node = resource.contents
        if isinstance(node, bool):
            return
        if node.get('$schema', _DRAFT).rstrip('#') != _DRAFT:
            raise ValueError('Only JSON Schema Draft2020-12 is supported')
        for key in ('$ref', '$dynamicRef'):
            if key in node:
                if not node[key].startswith('#'):
                    raise ValueError('External JSON schema references are not supported')
                resolver.lookup(node[key])
        _same_instance_schemas(node, resolver)
        for child in resource.subresources():
            self._check_resources(child, resolver.in_subresource(child))

    def validate(self, args):
        try:
            _finite_json(args)
        except (ValueError, RecursionError):
            raise JsonToolInputError('finite JSON') from None
        if not isinstance(args, dict):
            raise JsonToolInputError('object')
        if set(args) & RESERVED_ARGUMENTS:
            raise JsonToolInputError('reserved argument')
        try:
            error = next(self.validator.iter_errors(args), None)
        except (Unresolvable, RecursionError):
            raise ValueError('Registered tool JSON schema could not be evaluated') from None
        if error is not None:
            raise JsonToolInputError(error.validator, error.absolute_path)
        return RootModel[dict](deepcopy(args))
