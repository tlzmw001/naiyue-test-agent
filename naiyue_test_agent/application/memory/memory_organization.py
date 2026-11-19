"""Bounded model read/finish loop and dependency isolation before any commit."""
import json
from langchain_core.messages import HumanMessage,SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.agent_runtime.run_control import native_async_model
from naiyue_test_agent.agent_runtime.stream_events import MEMORY_TAG
from naiyue_test_agent.model_access import MODEL_ID_METADATA_KEY
from naiyue_test_agent.model_access.memory_output import prepare_memory_output
from naiyue_test_agent.contracts.memory import MemoryReadError
from .memory_organization_reads import Ledger
from .memory_organization_commit import commit_group
from .memory_wiki_protocol import OrganizationResult,ORGANIZE_PROMPT,parse,bind_extraction


async def organize(service,job,batch,raws):
    from .memory_job_runner import _limits,_check,_failure,_commit
    proposals={};work={}
    for index,raw in enumerate(raws,1):
        try:
            proposal=bind_extraction(raw,batch,index)
            proposals[proposal['proposal_id']]=proposal
            work.setdefault((proposal['scope'],proposal['topic']),{})[proposal['proposal_id']]=proposal
        except Exception as error:_failure(job,'memory_wiki_proposal_failed',error,stage='wiki_bind',proposal_index=index)
    explicit=job.wiki_request and job.wiki_request['action']=='organize'
    if explicit:
        scope=job.wiki_request['scope']
        work[(scope,None)]={}
    results=[]
    for (scope,topic),selected in work.items():
        ledger=Ledger(scope,topic,writable=job.wiki_request['page_ids'] if explicit else None)
        finished=None
        try:
            cap,output=_limits(job.model,service.settings)
            bound,mode,overhead=prepare_memory_output(job.model,OrganizationResult.model_json_schema(),output)
            system=SystemMessage(content=ORGANIZE_PROMPT+'\nOutput JSON schema:\n'+json.dumps(OrganizationResult.model_json_schema(),ensure_ascii=False))
            material=dict(scope=scope,topic=topic,writable_page_ids=sorted(ledger.writable) if explicit else None,
                proposals=list(selected.values()))
            messages=[system,HumanMessage(content=json.dumps(material,ensure_ascii=False))]
            used=0
            while True:
                _check(job)
                if count_tokens_approximately(messages)+overhead>cap:raise MemoryReadError('memory_organization_budget')
                config=dict(tags=[MEMORY_TAG],metadata={MODEL_ID_METADATA_KEY:job.model_id,'memory_task_id':job.task_id,
                    'memory_batch':job.batches,'memory_stage':'organize'})
                if native_async_model(job.model):response=await job.control.await_model(lambda:bound.ainvoke(messages,config=config))
                else:response=await run_blocking(bound.invoke,messages,config=config)
                _check(job)
                value=parse(response if mode=='text' else response['raw'],mode,OrganizationResult)
                step=OrganizationResult.model_validate(value).result
                if step.step=='finish':
                    finished=step.model_dump(mode='json');break
                replies=[]
                for request in step.requests:
                    _check(job)
                    reply=await run_blocking(ledger.read,service.memory,job.target_session_id,job.project_id,
                        request.model_dump(mode='json'),service.allowed)
                    text=json.dumps(reply,ensure_ascii=False);used+=len(text.encode())
                    if used>service.settings.comparison_material_bytes:raise MemoryReadError('memory_organization_budget')
                    replies.append(reply)
                # History of our protocol steps and bounded projections is local to this unit.
                messages.extend([HumanMessage(content=json.dumps(dict(previous_step=value,read_results=replies),ensure_ascii=False))])
        except Exception as error:
            _check(job)
            _failure(job,'memory_wiki_organization_failed',error,stage='wiki_read')
            job.skipped.append('wiki_organization_failed')
        results.append(dict(ledger=ledger,proposals=selected,finished=finished))
    # Include dependencies of failed units; do not remove them and permit another add.
    from .memory_organization_groups import connected_groups
    pending=connected_groups(results)
    while pending:
        group=pending.pop(0)
        if any(u['finished'] is None for u in group):continue
        _check(job)
        try:
            units=[(u['ledger'],u['proposals'],u['finished']) for u in group]
            await _commit(job,'saved',commit_group,service.memory,job.target_session_id,job.project_id,units,service.allowed)
            # Account only for our own known catalog increment; external revisions still fail CAS.
            changed=any(u['finished'].get('page_changes') or u['finished'].get('links') or any(d['relation'] not in ('duplicate','unsupported') for d in u['finished']['decisions']) for u in group)
            scope=group[0]['ledger'].scope
            state=group[0]['ledger'].states
            for unit in [u for group in pending for u in group]:
                ledger=unit['ledger']
                if changed and ledger.scope==scope and state is not None and ledger.states==state:ledger.states=[state[0],state[1]+1]
        except Exception as error:
            _check(job)
            _failure(job,'memory_wiki_commit_failed',error,stage='wiki_commit')
            job.skipped.append('wiki_commit_failed')
