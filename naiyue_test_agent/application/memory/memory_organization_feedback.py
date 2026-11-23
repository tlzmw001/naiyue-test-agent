"""Pre-write diagnostics for parameter errors in the Wiki organization protocol."""
from collections import Counter

from .memory_wiki_protocol import (
    BoundRead, BoundReadPage, BoundListPages, Decision, Finish, Link, PageChange, WikiProposal,
)


def schema_feedback(error):
    """Return only schema-owned field names and numeric positions."""
    fields = {'result', 'step', 'requests', 'action', 'page_id', 'memory_id',
              'cursor', 'version'}
    for model in (BoundRead, BoundReadPage, BoundListPages, Decision, Finish, PageChange, Link, WikiProposal):
        fields.update(model.model_fields)
    hints = []
    for issue in error.errors(include_input=False, include_url=False, include_context=False):
        if issue['type'] == 'extra_forbidden' and issue['loc'][-1] in ('scope', 'topic'):
            return []  # A request attempted to override a program-bound scope or topic.
        path = ''
        for key in issue['loc']:
            if isinstance(key, int):
                path += f'[{key}]'
            elif key in fields:
                path += ('.' if path else '') + key
        if len(hints) < 8:
            hint = f"{path or 'result'}: {issue['type']}. Follow the field definition in the supplied schema."
            if hint not in hints:
                hints.append(hint)
    return hints


def finish_feedback(plan, proposals):
    hints = []
    declared_pages = set()
    for index, change in enumerate(plan.page_changes):
        if change.ref.startswith('N') and change.ref in declared_pages:
            hints.append(f'page_changes[{index}].ref: define each new page ref once.')
        declared_pages.add(change.ref)
    consumed = Counter(pid for d in plan.decisions for pid in d.proposal_ids)
    if set(consumed) != set(proposals) or any(count != 1 for count in consumed.values()):
        hints.append('decisions[].proposal_ids: use each supplied P exactly once, including unsupported proposals.')
    for index, decision in enumerate(plan.decisions):
        relation = decision.relation
        prefix = f'decisions[{index}].'
        if len(set(decision.old_refs)) != len(decision.old_refs):
            hints.append(prefix + 'old_refs: list each previously read participant only once.')
        if relation in ('add', 'unresolved_conflict'):
            if decision.old_refs:
                hints.append(prefix + 'old_refs: a new record has no old participants.')
            if not decision.proposal_ids:
                hints.append(prefix + 'proposal_ids: a new record requires at least one supplied proposal.')
        if relation in ('move', 'split', 'supplement', 'confirm', 'correction') and len(decision.old_refs) != 1:
            hints.append(prefix + 'old_refs: this relation acts on exactly one previously read record.')
        if relation in ('move', 'split') and (decision.proposal_ids or decision.evidence_ids):
            hints.append(prefix + 'proposal_ids/evidence_ids: a move preserves the old record and takes no new evidence.')
        if relation == 'merge' and len(decision.old_refs) + len(decision.proposal_ids) < 2:
            hints.append(prefix + 'old_refs/proposal_ids: merge requires at least two participants including the retained old record.')
        if relation in ('add', 'unresolved_conflict', 'move', 'split') and not decision.destination:
            hints.append(prefix + 'destination: give the read page ID or the new page ref defined in page_changes.')
        elif (relation in ('add', 'unresolved_conflict', 'move', 'split', 'merge') and decision.destination
              and decision.destination.startswith('N') and decision.destination not in declared_pages):
            hints.append(prefix + 'destination: the selected new page ref must be defined in page_changes.')
        if relation in ('duplicate', 'supplement', 'confirm', 'correction', 'merge', 'move', 'split'):
            if not decision.target_old_ref or decision.target_old_ref not in decision.old_refs:
                hints.append(prefix + 'target_old_ref: choose a previously read O and include it in old_refs.')
        if relation in ('merge', 'correction') and decision.proposal is None:
            hints.append(prefix + 'proposal: include the rewritten proposal required for this relation.')
        chosen = [proposals[pid] for pid in decision.proposal_ids if pid in proposals]
        if relation in ('add', 'unresolved_conflict') and len(chosen) > 1 and decision.proposal is None:
            hints.append(prefix + 'proposal: supply the combined proposal or keep independent proposals separate.')
        if relation not in ('unsupported', 'duplicate'):
            available = {eid for proposal in chosen for eid in proposal['evidence_ids']}
            refs = set(decision.evidence_ids)
            inherited = relation in ('add', 'unresolved_conflict') and len(chosen) == 1 and decision.proposal is None
            if relation == 'merge' and any(not refs.intersection(p['evidence_ids']) for p in chosen):
                hints.append(prefix + 'evidence_ids: cite actual supporting evidence for each new proposal in the merge, or keep unsupported proposals separate.')
            elif (len(refs) != len(decision.evidence_ids) or not refs <= available
                    or (chosen and not refs and not inherited and relation not in ('move', 'split'))):
                hints.append(prefix + 'evidence_ids: cite only supporting E IDs bound to the chosen P. Use unsupported if evidence is insufficient.')
        if len(hints) >= 8:
            break
    for index, link in enumerate(plan.links):
        if link.page.startswith('N') and link.page not in declared_pages:
            hints.append(f'links[{index}].page: define the new page ref in page_changes.')
        if any(ref.startswith('N') and ref not in declared_pages for ref in link.targets):
            hints.append(f'links[{index}].targets: define referenced new pages in page_changes.')
    return hints[:8]
