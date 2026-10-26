"""Bound SQL over one memory authority; caller owns its short read transaction."""

import json

from naiyue_test_agent.contracts import memory_lexical as lexical




STATUS = {
    'active': "m.status='active' AND (m.expires_at IS NULL OR m.expires_at>?)",
    'candidate': "m.status='candidate' AND (m.expires_at IS NULL OR m.expires_at>?)",
    'inactive': "m.status='inactive' AND ? IS NOT NULL",
    'expired': "m.status IN ('active','candidate') AND m.expires_at<=?",
    'all': "m.status!='forgotten' AND ? IS NOT NULL",
}
from naiyue_test_agent.contracts.memory import MemoryReadError, CONDITIONS


def eligibility(scope, workspace_id, request, now):
    # SQL expressions/field names are fixed source; every value is bound.
    sql = "m.scope=? AND (?='personal' OR m.workspace_id=?) AND " + STATUS[request['status']]
    parameters = [scope, scope, workspace_id, now]
    sql += ' AND (? IS NULL OR m.kind=?)'
    parameters.extend([request['kind'], request['kind']])
    for field in CONDITIONS:
        sql += f' AND (m.{field} IS NULL OR ? IS NULL OR m.{field}=?)'
        parameters.extend([request[field], request[field]])
    return sql, parameters


def current(connection, scope, workspace_id, request, now, identity):
    where, values = eligibility(scope, workspace_id, request, now)
    row = connection.execute('SELECT m.* FROM naiyue_memories m WHERE ' + where + ' AND m.memory_id=?',
                             (*values, identity)).fetchone()
    return dict(row) if row else None


def listing(connection, scope, workspace_id, request, now, before, limit):
    where, values = eligibility(scope, workspace_id, request, now)
    if before:
        timestamp, before_scope, identity = before
        where += ' AND (m.updated_at<? OR (m.updated_at=? AND (m.scope>? OR (m.scope=? AND m.memory_id>?))))'
        values.extend((timestamp, timestamp, before_scope, before_scope, identity))
    return [dict(row) for row in connection.execute('SELECT m.* FROM naiyue_memories m WHERE ' + where
        + ' ORDER BY m.updated_at DESC,m.scope,m.memory_id LIMIT ?', (*values, limit))]


def rank(connection, scope, workspace_id, request, now, variant, settings):
    terms = lexical.tokens(variant)
    if len(terms) > settings.max_query_terms:
        raise MemoryReadError('memory_query_too_complex')
    if not terms:
        return [], False
    where, values = eligibility(scope, workspace_id, request, now)
    required = sorted(lexical.anchors(request['query']) | lexical.anchors(variant))
    expression = ' OR '.join('"' + term + '"' for term in terms)
    rows = connection.execute('''SELECT m.*,bm25(naiyue_memory_search,?,?,?) AS local_score
        FROM naiyue_memory_search JOIN naiyue_memories m ON m.rowid=naiyue_memory_search.rowid
        WHERE naiyue_memory_search MATCH ? AND ''' + where + '''
        AND memory_anchors(m.title,m.aliases_json,m.body,?)=1
        AND m.memory_id=naiyue_memory_search.memory_id AND m.version=naiyue_memory_search.memory_version
        AND memory_digest(m.title,m.aliases_json,m.body)=naiyue_memory_search.source_digest
        ORDER BY local_score,m.memory_id LIMIT ?''',
        (*settings.field_weights, expression, *values, json.dumps(required), settings.candidate_limit + 1)).fetchall()
    return [dict(row) for row in rows[:settings.candidate_limit]], len(rows) > settings.candidate_limit


def sources(connection, identity, version, limit):
    rows = connection.execute('''SELECT * FROM naiyue_memory_sources
        WHERE memory_id=? AND memory_version=? ORDER BY source_id LIMIT ?''', (identity, version, limit + 1)).fetchall()
    return [dict(row) for row in rows[:limit]], len(rows) > limit
