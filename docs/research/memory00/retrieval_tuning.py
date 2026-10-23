"""Deterministic candidate selection for offline experiments only."""

import math

from .retrieval import CHINESE, LEXEMES, adapted_tokens, anchors


ROUTES = ('substring', 'adapted_or', 'coverage_rrf', 'cascade_rrf', 'cascade_quality')


def features(row, query, weights):
    units = tuple(dict.fromkeys(LEXEMES.findall(query)))
    query_terms = set(adapted_tokens(query))
    fields = [row[field] for field in ('title', 'aliases', 'body')]
    available_anchors = set(LEXEMES.findall(' '.join(fields)))
    if not set(anchors(query)) <= available_anchors:
        return {'covered': 0, 'terms': len(query_terms), 'literal': False, 'quality': 0}
    field_tokens = [set(adapted_tokens(field)) for field in fields]
    available = set().union(*field_tokens)
    covered = len(query_terms & available)
    literal_ratios = []
    all_literals = set()
    for field in fields:
        # Chinese uses intact raw phrases; English uses complete lexer words.
        words = {word.casefold() for word in LEXEMES.findall(field)}
        matched = {unit for unit in units if
                   (unit in field if CHINESE.fullmatch(unit) else unit.casefold() in words)}
        literal_ratios.append(len(matched) / len(units) if units else 0)
        all_literals.update(matched)
    literal_strength = max((weight * ratio for weight, ratio in zip(weights, literal_ratios)), default=0)
    weighted_coverage = sum(weight * len(query_terms & terms) / len(query_terms)
        for weight, terms in zip(weights, field_tokens)) / sum(weights) if query_terms else 0
    return {'covered': covered, 'terms': len(query_terms),
            'literal': bool(units) and len(all_literals) == len(units),
            'quality': literal_strength + weighted_coverage}


def select_results(candidates, query, *, route, coverage=(2, 3), weights=(3, 2, 1), limit=8, expansions=()):
    if route not in ROUTES:
        raise ValueError('unknown route')
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('invalid result budget')
    if (len(coverage) != 2 or any(type(n) is not int or n <= 0 for n in coverage)
            or coverage[0] > coverage[1]):
        raise ValueError('invalid coverage fraction')
    if len(weights) != 3 or any(not math.isfinite(w) or w <= 0 for w in weights):
        raise ValueError('invalid field weights')
    if route in ('substring', 'adapted_or'):
        return candidates[:limit]
    variants = tuple(dict.fromkeys((query, *expansions)))
    measured = [(row, max((features(row, variant, weights) for variant in variants),
                          key=lambda f: (f['literal'], f['covered'] / f['terms'] if f['terms'] else 0,
                                         f['quality']))) for row in candidates]
    precise = [item for item in measured if item[1]['literal']]
    if route.startswith('cascade') and precise:
        selected = precise
    else:
        numerator, denominator = coverage
        selected = [(row, f) for row, f in measured if f['terms'] and
                    f['covered'] * denominator >= f['terms'] * numerator]
    if route == 'cascade_quality':
        selected.sort(key=lambda item: (-item[1]['quality'], -item[0]['score'],
                                        item[0]['scope'], item[0]['id']))
    return [{**row, 'match_coverage': f['covered'] / f['terms'],
             'literal_match': f['literal'], 'quality_score': f['quality']}
            for row, f in selected[:limit]]
