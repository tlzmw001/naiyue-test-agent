"""Application selection policy over shared lexical facts."""

from naiyue_test_agent.contracts.memory_lexical import LEXEMES, CHINESE, alias_text, anchors, tokens


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
