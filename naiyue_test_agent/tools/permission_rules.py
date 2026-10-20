"""Strict, immutable rule configuration and component-based path matching."""

import math
import os
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from .paths import permission_path


class PermissionRule(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    tool: str = Field(min_length=1)
    path: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)

    @field_validator('args')
    @classmethod
    def scalar_arguments(cls, values):
        if any(type(value) not in (str, int, float, bool, type(None))
               or isinstance(value, float) and not math.isfinite(value) for value in values.values()):
            raise ValueError('Permission arguments must be finite JSON scalars')
        return values

    @field_validator('path')
    @classmethod
    def path_pattern(cls, value):
        if value is None:
            return value
        if value.startswith('${workspace}'):
            if os.name == 'nt':
                permission_path('C:/workspace' + value[len('${workspace}'):], windows=True, pattern=True)
        elif os.name == 'nt' or PureWindowsPath(value).drive or value.startswith(('\\\\', '//')):
            permission_path(value, windows=True, pattern=True)
        tail = value
        if value.startswith('${workspace}'):
            tail = value[len('${workspace}'):]
            if tail and not tail.startswith(('/', '\\')):
                raise ValueError('Invalid workspace path pattern')
        elif not (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()):
            raise ValueError('Permission path must be absolute or start with ${workspace}')
        if any(char in tail for char in '\x00[]{}') or '$' in tail:
            raise ValueError('Unsupported permission path syntax')
        parts = tail.replace('\\', '/').split('/')
        if any(part == '..' or '**' in part and part != '**' for part in parts):
            raise ValueError('Invalid permission path component')
        return value


class AutoPermissionSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    fast_model_id: str = Field(min_length=1)
    detailed_model_id: str = Field(min_length=1)
    max_reason_chars: int = Field(default=512, gt=0)


class PermissionSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    mode: Literal['Default', 'NoAsk', 'Auto', 'BypassApprovals'] = 'Default'
    auto: AutoPermissionSettings | None = None
    deny: tuple[PermissionRule, ...] = ()
    allow: tuple[PermissionRule, ...] = ()

    def validate_mode(self, mode):
        if mode not in ('Default', 'NoAsk', 'Auto', 'BypassApprovals') or mode == 'Auto' and self.auto is None:
            raise ValueError('Permission mode is unavailable or Auto models are not configured')

    @model_validator(mode='after')
    def available_mode(self):
        self.validate_mode(self.mode)
        return self

    @field_validator('deny', 'allow', mode='before')
    @classmethod
    def ordered_rules(cls, value):
        return tuple(value) if isinstance(value, list) else value


def validate_permission_rules(settings, specs):
    for rule in (*settings.deny, *settings.allow):
        candidates = [spec for spec in specs if rule.tool in ('*', spec.tool.name)]
        if not candidates:
            raise ValueError('Permission rule refers to an unknown tool')
        if rule.path is not None:
            files = [spec for spec in candidates if getattr(spec.permissions, 'path_field', None)]
            if not files:
                raise ValueError('Permission path requires a file tool')
            if rule.tool != '*' and len(files) != len(candidates):
                raise ValueError('Permission path requires a file tool')
            candidates = files
        for name, value in rule.args.items():
            annotations = []
            for spec in candidates:
                if isinstance(spec.input_schema, dict):
                    raise ValueError('JSON schema tools do not support argument permission rules')
                field = spec.input_schema.model_fields.get(name)
                if field is None:
                    raise ValueError('Permission rule refers to an unknown public argument')
                annotation = Annotated[field.annotation, *field.metadata] if field.metadata else field.annotation
                TypeAdapter(annotation).validate_python(value, strict=True)
                annotations.append(field.annotation)
            if any(annotation != annotations[0] for annotation in annotations):
                raise ValueError('Wildcard argument types must match')


def _component_match(pattern, path, windows):
    if windows:
        pattern = permission_path(pattern, windows=True, pattern=True).as_posix()
        path = permission_path(path, windows=True).as_posix()
    pattern_parts, path_parts = pattern.split('/'), path.split('/')
    positions = {0}
    for part in pattern_parts:
        if part == '**':
            positions = {end for start in positions for end in range(start, len(path_parts) + 1)}
        else:
            expression = ''.join('.*' if char == '*' else '.' if char == '?' else re.escape(char) for char in part)
            positions = {index + 1 for index in positions
                         if index < len(path_parts) and re.fullmatch(expression, path_parts[index]) is not None}
    return len(path_parts) in positions


def match_rules(rules, subject, *, deny):
    for rule in rules:
        if rule.tool not in ('*', subject['tool_name']):
            continue
        args = subject['args']
        if any(name not in args or type(args[name]) is not type(value) or args[name] != value
               for name, value in rule.args.items()):
            continue
        if rule.path is not None:
            if 'resolved' not in subject:
                continue
            workspace = subject.get('workspace')
            if rule.path.startswith('${workspace}') and workspace is None:
                continue
            pattern = rule.path.replace('${workspace}', workspace or '')
            targets = (subject['requested'], subject['resolved']) if deny else (subject['resolved'],)
            windows = subject.get('execution_environment', {}).get('platform') == 'windows'
            if not any(_component_match(pattern, path, windows) for path in targets):
                continue
        return rule
    return None
