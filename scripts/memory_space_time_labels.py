"""Strict v2 synthetic evaluation contract, consumed by load_suite."""


def validate(data, protocol):
    if data.get('format') != 2 or data.get('synthetic') is not True or protocol.get('format') != 2:
        raise ValueError('fixture_invalid')
    cases = data.get('cases', [])
    if len(cases) != 24 or protocol.get('max_logical_calls') != 24 or protocol.get('calls_per_case') != 3:
        raise ValueError('fixture_balance_or_budget')
    ids, families = set(), set()
    flags = ('must_extract', 'must_activate', 'must_inject', 'must_not_activate')
    for c in cases:
        if c['case_id'] in ids or c['family_id'] in families:
            raise ValueError('fixture_identity_or_split_leakage')
        ids.add(c['case_id']); families.add(c['family_id'])
        if c['suite'] != 'semantic' or not c['followup_query'].strip() or not c['answer_requirements']:
            raise ValueError('fixture_followup_missing')
        sources = {s['id']: s['text'] for s in c['sources']}
        if len(sources) != len(c['sources']) or not sources or not c['expected_facts']:
            raise ValueError('fixture_labels_missing')
        aliases = set()
        for old in c['old_memories']:
            required = ('alias', 'scope', 'legacy_kind', 'time_kind', 'body', 'subject', 'source', 'age_seconds', 'source_available')
            if any(k not in old for k in required) or old['alias'] in aliases:
                raise ValueError('fixture_seed_invalid')
            if old['scope'] not in ('personal', 'workspace') or old['time_kind'] not in ('convention', 'context'):
                raise ValueError('fixture_seed_invalid')
            if not old['source']['text'] or old['body'] not in old['source']['text']:
                raise ValueError('fixture_seed_source_missing')
            aliases.add(old['alias'])
        facts = set()
        for f in c['expected_facts']:
            if f['fact_id'] in facts or any(type(f.get(k)) is not bool for k in flags):
                raise ValueError('fixture_fact_invalid')
            facts.add(f['fact_id'])
            if f['must_not_activate'] and (f['must_activate'] or f['must_inject']):
                raise ValueError('fixture_labels_conflict')
            for key, allowed in (('allowed_scopes', {'personal', 'workspace'}),
                                 ('allowed_time_kinds', {'convention', 'context'})):
                if not f[key] or not set(f[key]) <= allowed:
                    raise ValueError('fixture_policy_invalid')
            if f['target_old'] is not None and f['target_old'] not in aliases:
                raise ValueError('fixture_seed_target_missing')
            if not f['claim'] or not f['evidence']:
                raise ValueError('fixture_evidence_missing')
            for e in f['evidence']:
                text = sources.get(e['source_id'])
                if text is None or type(e['start']) is not int or type(e['end']) is not int or not 0 <= e['start'] < e['end'] <= len(text):
                    raise ValueError('fixture_evidence_invalid')
    for split in ('calibration', 'validation', 'holdout'):
        if sum(c['split'] == split for c in cases) != 8:
            raise ValueError('fixture_balance')
    return cases, protocol
