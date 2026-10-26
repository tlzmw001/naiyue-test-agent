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
