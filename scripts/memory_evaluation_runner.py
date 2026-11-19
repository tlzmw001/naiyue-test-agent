"""Run fixed cases through production archive, write and injection entrypoints."""
import json
from pathlib import Path
import tempfile
import time


def execute_case(case):
    from scripts.memory_evaluation_cases import seed_sources
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.application.memory.memory_access import MemoryAccess
    from naiyue_test_agent.application.memory.memory_batches import build_batch
    from scripts.memory_legacy_recall import prepare_memory_parts
    from naiyue_test_agent.tools.memory import MemoryInput
    from naiyue_test_agent.memory_settings import MemorySettings
    from langchain_core.messages import HumanMessage
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='nyt-optimization-') as directory:
        root=Path(directory).resolve()
        with ProjectStore(root/'test.sqlite') as store:
            project='11111111-1111-1111-1111-111111111111'
            store.sessions.bind_workspace(project,root)
            store.sessions.create('probe',dict(thread_id='probe',model_id='offline',workspace_root=root,
                active_tool_names=['memory'],permission_mode='Default'))
            settings=MemorySettings(auto_enabled=False,personal_convention_tokens=0)
            memory=MemoryAccess(store,settings,root/'data')
            try:
                source_time=time.time()-100
                facts=seed_sources(store,'probe',case['sources'],at=source_time)
                batch=build_batch(store.memory,session_id='probe',thread_id='probe',conversation_revision=0,
                    start=(0,0),upper=facts['upper'],token_budget=16000,source_chars=5,
                    max_messages=128,max_read_chars=65536,overlap_chars=600,context_floor=1)
                units=[u for u in batch['units'] if u['origin']=='new' and u['text']]
                suite=case['suite'];expected=case['must_activate']
                if suite=='input':
                    observed=''.join(u['text'] for u in units)
                    from naiyue_test_agent.application.memory.memory_batches import bind_proposal_evidence
                    from naiyue_test_agent.application.memory.memory_sources import resolve,SECRET
                    refs=bind_proposal_evidence({'sources':[dict(evidence_id=u['evidence_id']) for u in units if u['evidence_id']]},batch)['sources']
                    with store.memory.batch_reader(SECRET) as reader:
                        resolved=resolve(reader,project,'probe',refs,source_time+1)
                    supports=[f['support'] for f in case['expected_facts'] if f['must_extract']]
                    covered=all(all(any(r['message_id']==support['message_id']
                        and r['start_offset']<=offset<r['end_offset'] for r in resolved)
                        for offset in range(support['offset'],support['offset']+len(support['quote'])))
                        for support in supports)
                    passed=observed==case['sources'][0]['text'] and covered and len(resolved)==len(refs)
                    output=dict(resolved_spans=len(resolved),expected_evidence_ranges=supports,evidence_covered=covered,reconstructed=observed,read_ranges=batch['coverage']['read_ranges'],complete=batch['complete'])
                    entry='build_batch'
                elif suite=='semantic':
                    return dict(case_id=case['case_id'],suite=suite,execution_status='unattempted',
                        passed=None,reason='semantic_pending',entrypoint='MemoryJobs (provider not requested)')
                else:
                    proposal=dict(kind='context',subject=case['family_id'],body=case['claim'] if suite=='comparison' else case['sources'][0]['text'],
                        basis='user_declaration',intent='activate',sources=[dict(evidence_id=u['evidence_id'])
                            for u in units if u['evidence_id']])
                    # Controlled public writes remain executable on the frozen pre-S3 source.
                    # They do not sample or claim to exercise B; the S3 matrix uses the worker.
                    from naiyue_test_agent.application.memory.memory_batches import bind_proposal_evidence
                    refs=bind_proposal_evidence(proposal,batch)['sources']
                    request=MemoryInput(action='record',scope='workspace',sources=refs,
                        proposal={k:v for k,v in proposal.items() if k!='sources'} | dict(
                            title=case['family_id'],reason='Controlled S0 proposal')).model_dump(mode='json')
                    result=memory.query('probe',project,request,now=source_time+1)
                    if suite=='comparison':
                        passed=(result['item']['status']=='active')==expected
                        output=dict(status=result['item']['status'],prohibited_activation=not expected and result['item']['status']=='active',
                            B='N/A',relation_judgment='not_sampled_controlled_proposal')
                        entry='MemoryInput+MemoryAccess.query'
                    else:
                        parts=prepare_memory_parts(memory,'probe',project,[HumanMessage(content=case['query'])],None)
                        ids=[]
                        for part in parts:
                            if part['id']=='memory':
                                payload=json.loads(part['text'][part['text'].index('{'):])
                                ids.extend(r['memory_id'] for r in payload['related'])
                        hit=result['item']['memory_id'] in ids
                        passed=hit
                        output=dict(relevant_found=int(hit),relevant_total=1,injected=len(ids),recall=float(hit),
                            precision=float(hit)/len(ids) if ids else None)
                        entry='prepare_memory_parts'
            finally:memory.close()
    return dict(case_id=case['case_id'],suite=suite,entrypoint=entry,execution_status='completed',
        passed=passed,observed=output,source_time=source_time,elapsed_seconds=time.monotonic()-started)
