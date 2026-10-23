"""Standard-library lexical experiment; not a product store or memory API."""

from datetime import datetime
import hashlib
import json
import math
import re
import sqlite3


METHODS = ('substring', 'unicode61', 'adapted')
LEXEMES = re.compile(
    r'[\u3400-\u4dbf\u4e00-\u9fff]+'
    r'|(?:[a-zA-Z]:[\\/]|[./\\])?[a-zA-Z0-9_]+(?:[./:\\-][a-zA-Z0-9_]+)*')
CHINESE = re.compile(r'^[\u3400-\u4dbf\u4e00-\u9fff]+$')


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp requires timezone')
    return parsed.timestamp()


def is_anchor(term):
    return (any(char in term for char in '_./\\:') or term.isascii() and term.isdigit()
        or '-' in term and any(char.isdigit() for char in term))


def anchors(text):
    return tuple(term for term in LEXEMES.findall(text) if is_anchor(term))


def adapted_tokens(text):
    terms = []
    for word in LEXEMES.findall(text):
        if CHINESE.fullmatch(word):
            parts = [word[i:i + 2] for i in range(len(word) - 1)] if len(word) > 1 else [word]
        else:
            parts = [word if is_anchor(word) else word.casefold()]
        # Hex preserves identifier boundaries against unicode61's punctuation splitting.
        terms.extend('z' + part.encode('utf-8').hex() for part in parts)
    return tuple(dict.fromkeys(terms))


def _has_anchors(title, aliases, body, requested):
    available = set(LEXEMES.findall(' '.join((title, aliases, body))))
    return int(set(json.loads(requested)) <= available)


def content_digest(title, aliases, body):
    return hashlib.sha256(json.dumps((title, aliases, body), ensure_ascii=False).encode('utf-8')).hexdigest()


def _quoted_match(terms):
    return ' OR '.join('"' + term.replace('"', '""') + '"' for term in dict.fromkeys(terms))


class ResearchIndex:
    def __init__(self, rows, *, method, weights=(5, 3, 1), rrf_k=60, candidate_limit=50):
        if method not in METHODS:
            raise ValueError('unknown method')
        if len(weights) != 3 or any(not math.isfinite(w) or w <= 0 for w in weights):
            raise ValueError('weights must be positive finite numbers')
        if type(rrf_k) is not int or rrf_k <= 0 or type(candidate_limit) is not int or candidate_limit <= 0:
            raise ValueError('invalid candidate/rrf budget')
        self.method, self.weights = method, tuple(weights)
        self.rrf_k, self.candidate_limit = rrf_k, candidate_limit
        self.connections = {}
        try:
            for scope in ('workspace', 'personal'):
                connection = sqlite3.connect(':memory:')
                self.connections[scope] = connection
                connection.row_factory = sqlite3.Row
                connection.create_function('has_anchors', 4, _has_anchors, deterministic=True)
                connection.create_function('content_digest', 3, content_digest, deterministic=True)
                connection.executescript('''
                    CREATE TABLE memories(id TEXT PRIMARY KEY, scope TEXT, workspace_id TEXT,
                        kind TEXT, status TEXT, forgotten INTEGER, machine TEXT, target TEXT, module TEXT,
                        expires_at REAL, title TEXT, aliases TEXT, body TEXT, version INTEGER);
                    CREATE VIRTUAL TABLE search USING fts5(title, aliases, body,
                        memory_version UNINDEXED, source_digest UNINDEXED, tokenize='unicode61');
                ''')
            for source in rows:
                self._insert(source)
        except BaseException:
            self.close()
            raise

    def _insert(self, source):
        scope = source['scope']
        if scope not in self.connections:
            raise ValueError('unknown scope')
        if scope == 'workspace' and not source.get('workspace_id'):
            raise ValueError('workspace record requires identity')
        if source['status'] not in ('active', 'candidate', 'inactive'):
            raise ValueError('unknown status')
        fields = (source['title'], ' '.join(source.get('aliases', [])), source['body'])
        if any(not isinstance(value, str) for value in fields):
            raise ValueError('memory text must be strings')
        expiry = timestamp(source['expires_at']) if source.get('expires_at') else None
        version = source.get('version', 1)
        if type(version) is not int or version < 1:
            raise ValueError('positive memory version required')
        connection = self.connections[scope]
        cursor = connection.execute('''INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (source['id'], scope, source.get('workspace_id'), source['kind'], source['status'],
             int(source.get('forgotten', False)), source.get('machine'), source.get('target'), source.get('module'),
             expiry, *fields, version))
        indexed = tuple(' '.join(adapted_tokens(value)) for value in fields) if self.method == 'adapted' else fields
        connection.execute('INSERT INTO search(rowid,title,aliases,body,memory_version,source_digest) VALUES(?,?,?,?,?,?)',
                           (cursor.lastrowid, *indexed, version, content_digest(*fields)))

    @staticmethod
    def _parameters(context, as_of, exact):
        workspace = context.get('workspace_id')
        if not isinstance(workspace, str) or not workspace:
            raise ValueError('trusted workspace identity required')
        machine, target, module = context.get('machine'), context.get('target'), context.get('module')
        return (workspace, timestamp(as_of), machine, machine, target, target, module, module, json.dumps(exact))

    def _eligible(self, connection, parameters):
        return connection.execute('''SELECT m.* FROM memories m
            WHERE (m.scope='personal' OR m.workspace_id=?)
            AND m.status='active' AND m.forgotten=0 AND (m.expires_at IS NULL OR m.expires_at>?)
            AND (m.machine IS NULL OR ? IS NULL OR m.machine=?)
            AND (m.target IS NULL OR ? IS NULL OR m.target=?)
            AND (m.module IS NULL OR ? IS NULL OR m.module=?)
            AND has_anchors(m.title,m.aliases,m.body,?)=1''', parameters).fetchall()

    def _rank(self, connection, text, parameters):
        if self.method == 'substring':
            units = tuple(dict.fromkeys(text.casefold().split()))
            ranked = []
            for row in self._eligible(connection, parameters):
                fields = (row['title'], row['aliases'], row['body'])
                score = sum(weight * sum(unit in field.casefold() for unit in units)
                    for weight, field in zip(self.weights, fields))
                if score:
                    ranked.append((score, row))
            ranked.sort(key=lambda item: (-item[0], item[1]['id']))
            return [item[1] for item in ranked[:self.candidate_limit]]
        terms = adapted_tokens(text) if self.method == 'adapted' else LEXEMES.findall(text)
        expression = _quoted_match(terms)
        if not expression:
            return []
        return connection.execute('''SELECT m.*,bm25(search,?,?,?) AS local_score
            FROM search JOIN memories m ON m.rowid=search.rowid
            WHERE search MATCH ? AND (m.scope='personal' OR m.workspace_id=?)
            AND m.status='active' AND m.forgotten=0 AND (m.expires_at IS NULL OR m.expires_at>?)
            AND (m.machine IS NULL OR ? IS NULL OR m.machine=?)
            AND (m.target IS NULL OR ? IS NULL OR m.target=?)
            AND (m.module IS NULL OR ? IS NULL OR m.module=?)
            AND has_anchors(m.title,m.aliases,m.body,?)=1
            AND m.version=search.memory_version
            AND content_digest(m.title,m.aliases,m.body)=search.source_digest
            ORDER BY local_score,m.id LIMIT ?''',
            (*self.weights, expression, *parameters, self.candidate_limit)).fetchall()

    def search(self, query, context, as_of, *, limit=8, expansions=()):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('limit must be between 1 and 50')
        if not isinstance(query, str) or len(query) > 2000:
            raise ValueError('query must be bounded text')
        if len(expansions) > 5 or any(not isinstance(q, str) or len(q) > 2000 for q in expansions):
            raise ValueError('expansions must be bounded text')
        if not query.strip():
            return []
        parameters = self._parameters(context, as_of, anchors(query))
        contributions = {}
        for variant in dict.fromkeys((query, *expansions)):
            variant_parameters = self._parameters(context, as_of, tuple(dict.fromkeys((*anchors(query), *anchors(variant)))))
            for scope, connection in self.connections.items():
                for rank, row in enumerate(self._rank(connection, variant, variant_parameters), 1):
                    key = (scope, row['id'])
                    proof = (row['version'], content_digest(row['title'], row['aliases'], row['body']))
                    contributions.setdefault(key, []).append((proof, 1 / (self.rrf_k + rank)))
        # Current authority, never old FTS text, determines final visibility and text.
        current = {(scope, row['id']): dict(row) for scope, connection in self.connections.items()
                   for row in self._eligible(connection, parameters)}
        scores = {}
        for key, row in current.items():
            proof = (row['version'], content_digest(row['title'], row['aliases'], row['body']))
            score = sum(value for matched, value in contributions.get(key, ()) if matched == proof)
            if score:
                scores[key] = score
        result = []
        for key in sorted(scores, key=lambda key: (-scores[key], key)):
            if key not in current:
                continue
            row = current[key]
            row['score'] = scores[key]
            row['needs_verification'] = any(row[field] is not None and context.get(field) is None
                                            for field in ('machine', 'target', 'module'))
            result.append(row)
            if len(result) == limit:
                break
        return result

    def close(self):
        for connection in self.connections.values():
            connection.close()
        self.connections.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False
