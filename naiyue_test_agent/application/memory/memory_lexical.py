"""00b lexical contract in production: no imports from research or models."""

import hashlib
import json
import re


LEXEMES = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff]+'
    r'|(?:[a-zA-Z]:[\\/]|[./\\])?[a-zA-Z0-9_]+(?:[./:\\-][a-zA-Z0-9_]+)*')
CHINESE = re.compile(r'^[\u3400-\u4dbf\u4e00-\u9fff]+$')


def is_anchor(term):
    return (any(c in term for c in '_./\\:') or term.isascii() and term.isdigit()
            or '-' in term and any(c.isdigit() for c in term))


def anchors(text):
    return set(word for word in LEXEMES.findall(text) if is_anchor(word))


def tokens(text):
    result = []
    for word in LEXEMES.findall(text):
        if CHINESE.fullmatch(word):
            parts = [word[i:i + 2] for i in range(len(word) - 1)] if len(word) > 1 else [word]
        else:
            parts = [word if is_anchor(word) else word.casefold()]
        result.extend('z' + part.encode().hex() for part in parts)
    return tuple(dict.fromkeys(result))


def alias_text(value):
    values = json.loads(value)
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise ValueError('memory_invalid_aliases')
    return ' '.join(values)


def digest(title, aliases, body):
    return hashlib.sha256(json.dumps((title, aliases, body), ensure_ascii=False).encode()).hexdigest()


def configure(connection):
    connection.create_function('memory_terms', 1, lambda text: ' '.join(tokens(text)), deterministic=True)
    connection.create_function('memory_aliases', 1, alias_text, deterministic=True)
    connection.create_function('memory_digest', 3, digest, deterministic=True)
    connection.create_function('memory_anchors', 4, lambda title, aliases, body, required:
        int(set(json.loads(required)) <= set(LEXEMES.findall(' '.join((title, alias_text(aliases), body))))),
        deterministic=True)


def feature(row, query):
    fields = (row['title'], alias_text(row['aliases_json']), row['body'])
    if not anchors(query) <= set(LEXEMES.findall(' '.join(fields))):
        return False, 0.0
    units = set(LEXEMES.findall(query))
    covered_units = set()
    for field in fields:
        words = {word.casefold() for word in LEXEMES.findall(field)}
        covered_units.update(unit for unit in units if
            (unit in field if CHINESE.fullmatch(unit) else unit.casefold() in words))
    terms = set(tokens(query))
    available = set(tokens(' '.join(fields)))
    return bool(units) and units == covered_units, len(terms & available) / len(terms) if terms else 0.0


def select(candidates, variants, *, limit, coverage):
    measured = []
    for row in candidates:
        features = [feature(row, variant) for variant in row['matched_variants'] if variant in variants]
        literal = any(f[0] for f in features)
        covered = max((f[1] for f in features), default=0.0)
        measured.append({**row, 'literal_match': literal, 'match_coverage': covered})
    precise = [row for row in measured if row['literal_match']]
    selected = precise or [row for row in measured if row['match_coverage'] >= coverage]
    groups, result = {}, []
    for row in selected[:limit]:
        key = tuple(row.get(field) for field in ('scope', 'workspace_id', 'kind', 'status', 'expires_at', 'machine', 'module',
            'target', 'subject', 'basis', 'title', 'body'))
        if key in groups:
            groups[key]['duplicate_ids'].append(row['memory_id'])
        else:
            copy = {**row, 'duplicate_ids': []}
            groups[key] = copy
            result.append(copy)
    return result, len(selected) > limit
