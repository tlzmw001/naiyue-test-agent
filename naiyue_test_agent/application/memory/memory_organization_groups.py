"""Compute atomic components from decisions and actual write targets."""

def connected_groups(results):
    nodes=[]
    for number,unit in enumerate(results):
        ledger=unit['ledger'];finished=unit['finished']
        if finished is None:
            nodes.append((unit,{('page',p) for p in ledger.touched}));continue
        local=[]
        for decision in finished['decisions']:
            keys={('proposal',number,p) for p in decision.get('proposal_ids',[])}
            for ref in decision.get('old_refs',[]):
                old=ledger.old.get(ref)
                keys.add(('page',old['page_id']) if old else ('old',number,ref))
            if decision.get('destination'):
                ref=decision['destination'];keys.add(('new',number,ref) if ref.startswith('N') else ('page',ref))
            local.append((dict(step='finish',decisions=[decision],page_changes=[],links=[]),keys))
        for field in ('page_changes','links'):
            for value in finished.get(field,[]):
                refs=[value['ref']] if field=='page_changes' else [value['page'],*value['targets']]
                keys={('new',number,r) if r.startswith('N') else ('page',r) for r in refs}
                local.append((dict(step='finish',decisions=[],page_changes=[value] if field=='page_changes' else [],links=[value] if field=='links' else []),keys))
        covered={p for f,_ in local for d in f['decisions'] for p in d.get('proposal_ids',[])}
        if covered!=set(unit['proposals']):
            nodes.append((unit,{('page',p) for p in ledger.touched}));continue
        for partial,keys in local:
            selected={p:unit['proposals'][p] for d in partial['decisions'] for p in d.get('proposal_ids',[]) if p in unit['proposals']}
            nodes.append((dict(unit,finished=partial,proposals=selected),keys))
    groups=[]
    while nodes:
        unit,keys=nodes.pop(0);group=[unit];changed=True
        while changed:
            changed=False
            for other,dependencies in list(nodes):
                if other['ledger'].scope==unit['ledger'].scope and keys&dependencies:
                    nodes.remove((other,dependencies));group.append(other);keys|=dependencies;changed=True
        # Reassemble fragments of the same read ledger before coverage validation.
        merged={}
        for item in group:
            key=id(item['ledger'])
            if key not in merged:
                merged[key]=dict(item,proposals=dict(item['proposals']),finished=None if item['finished'] is None else dict(step='finish',decisions=[],page_changes=[],links=[]))
            dest=merged[key]
            if item['finished'] is None:dest['finished']=None
            elif dest['finished'] is not None:
                dest['proposals'].update(item['proposals'])
                for field in ('decisions','page_changes','links'):dest['finished'][field].extend(item['finished'].get(field,[]))
        groups.append(list(merged.values()))
    return groups
