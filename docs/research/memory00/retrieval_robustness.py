"""Offline topic clues and redundancy control; no semantic merge guarantee."""

import math
import re

from .retrieval import LEXEMES, adapted_tokens, anchors
from .retrieval_tuning import features, select_results as baseline


ROUTES = ('cascade_base', 'coverage_pool', 'topical_pool', 'topical_exact', 'topical_diverse', 'cascade_exact')


def _best(row, variants, weights, required_anchors=()):
    if not set(required_anchors) <= set(LEXEMES.findall(' '.join(row[f] for f in ('title', 'aliases', 'body')))):
        return {'covered': 0, 'terms': 0, 'literal': False, 'quality': 0}
    return max((features(row, variant, weights) for variant in variants),
               key=lambda f: (f['covered'] / f['terms'] if f['terms'] else 0, f['quality']))


def _exact_key(row, *, literal=False):
    # 00c normalizes whitespace; literal mode also preserves code indentation.
    text = tuple(row[field] if literal else ' '.join(row[field].split()) for field in ('title', 'body'))
    boundary = tuple(row.get(field) for field in
                     ('scope', 'workspace_id', 'kind', 'machine', 'target', 'module'))
    return boundary + text


def _collapse(rows, *, literal=False):
    output, groups = [], {}
    for row in rows:
        key = _exact_key(row, literal=literal)
        if key in groups:
            groups[key]['duplicate_ids'].append(row['id'])
        else:
            copied = {**row, 'duplicate_ids': []}
            groups[key] = copied
            output.append(copied)
    return output


def _diversify(rows, relevance, weight, limit):
    remaining = list(rows)
    tokens = {r['id']: set(adapted_tokens(r['title'] + ' ' + r['body'])) for r in rows}
    selected = []
    while remaining and len(selected) < limit:
        def score(row):
            own = tokens[row['id']]
            similarity = max((len(own & tokens[r['id']]) / len(own | tokens[r['id']])
                              if own | tokens[r['id']] else 0 for r in selected), default=0)
            return (weight * relevance[row['id']] - (1 - weight) * similarity, row['score'])

        chosen = max(remaining, key=score)
        selected.append(chosen)
        remaining.remove(chosen)
    return selected


def select_results(candidates, query, *, route, coverage=(1, 2), weights=(1, 1, 1),
                   limit=8, expansions=(), diversity_lambda=0.8):
    if route not in ROUTES:
        raise ValueError('unknown robustness route')
    if not math.isfinite(diversity_lambda) or not 0 <= diversity_lambda <= 1:
        raise ValueError('invalid diversity weight')
    # Reuse existing validation and variant-anchor contract, not caller gold data.
    fallback = baseline(candidates, query, route='cascade_rrf', coverage=(1, 2),
                        weights=weights, limit=limit, expansions=expansions)
    if route == 'cascade_base':
        return fallback
    if route == 'cascade_exact':
        eligible = baseline(candidates, query, route='cascade_rrf', coverage=(1, 2),
                            weights=weights, limit=50, expansions=expansions)
        return _collapse(eligible, literal=True)[:limit]
    if route == 'coverage_pool':
        return baseline(candidates, query, route='coverage_rrf', coverage=(1, 2),
                        weights=weights, limit=limit, expansions=expansions)
    if len(coverage) != 2 or any(type(n) is not int or n <= 0 for n in coverage) or coverage[0] > coverage[1]:
        raise ValueError('invalid topic coverage')
    variants = tuple(dict.fromkeys((query, *expansions)))
    selected, relevance = [], {}
    for row in candidates:
        lead = re.split(r'[。；;！？!?\n，,]', row['body'], maxsplit=1)[0]
        topic = _best({**row, 'body': lead}, variants, weights, anchors(query))
        if topic['terms'] and topic['covered'] * coverage[1] >= topic['terms'] * coverage[0]:
            selected.append({**row, 'topic_coverage': topic['covered'] / topic['terms']})
            full = _best(row, variants, weights)
            relevance[row['id']] = full['covered'] / full['terms'] if full['terms'] else 0
    if not selected:
        # No topic proof: retain the existing bounded body lookup.
        selected = baseline(candidates, query, route='cascade_rrf', coverage=(1, 2),
                            weights=weights, limit=50, expansions=expansions)
        for r in selected:
            full = _best(r, variants, weights)
            relevance[r['id']] = full['covered'] / full['terms'] if full['terms'] else 0
    if route in ('topical_exact', 'topical_diverse'):
        selected = _collapse(selected)
    if route == 'topical_diverse':
        return _diversify(selected, relevance, diversity_lambda, limit)
    return selected[:limit]
