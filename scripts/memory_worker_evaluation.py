"""Versioned synthetic action calibration through MemoryJobs; no provider or user config."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import time
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from scripts.memory_evaluation_cases import ROOT, write_report


class ScriptedModel(BaseChatModel):
    replies: list = Field(default_factory=list)
    calls: int = 0

    @property
    def _llm_type(self):return 'synthetic-memory-evaluation'

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls+=1
        if not self.replies:raise RuntimeError('unexpected_model_call')
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.replies.pop(0)))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages,stop,run_manager,**kwargs)


async def execute(case):
    from scripts.memory_evaluation_cases import seed_sources
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.application.memory.memory_access import MemoryAccess
    from naiyue_test_agent.application.memory.memory_jobs import MemoryJobs
    from naiyue_test_agent.application.memory.memory_batches import build_batch, bind_proposal_evidence
    from scripts.memory_legacy_recall import prepare_memory_parts
    from naiyue_test_agent.tools.memory import MemoryInput
    from naiyue_test_agent.memory_settings import MemorySettings
    from naiyue_test_agent.model_access import ModelCatalog
    now=time.time()
    with tempfile.TemporaryDirectory(prefix='nyt-worker-evaluation-') as directory:
        root=Path(directory).resolve()
        with ProjectStore(root/'sessions.sqlite') as store:
            store.sessions.bind_workspace(str(uuid4()),root)
            store.sessions.create('probe',dict(thread_id='probe',model_id='offline',workspace_root=root,
                active_tool_names=['memory'],permission_mode='Default'))
            settings=MemorySettings(auto_enabled=False,max_batches=1,personal_convention_tokens=0)
            model=ScriptedModel();catalog=ModelCatalog('offline',{'offline':model})
            memory=MemoryAccess(store,settings,root/'data');jobs=MemoryJobs(memory,catalog)
            def batch():
                return build_batch(store.memory,session_id='probe',thread_id='probe',conversation_revision=0,
                    start=(0,0),upper=100,token_budget=16000,source_chars=4096,max_messages=128,
                    max_read_chars=65536,overlap_chars=600,context_floor=1)
            def proposal(body,eid):
                return dict(matter=case.get('matter','本地回归环境'),kind='context',title='本地回归环境',subject=case.get('matter','本地回归环境'),
                    body=body,basis='user_declaration',intent='activate',reason='固定合成提议',sources=[dict(evidence_id=eid)])
            first=None
            try:
                if case['old_body']:
                    seed_sources(store,'probe',[dict(id='old',role='user',text=case['old_body'])],at=now-case['old_age'])
                    b=batch();p=proposal(case['old_body'],b['units'][0]['evidence_id'])
                    refs=bind_proposal_evidence(p,b)['sources']
                    first=memory.query('probe',store.sessions.get('probe')['project_id'],MemoryInput(action='record',scope='workspace',
                        proposal={k:v for k,v in p.items() if k not in ('sources','matter')},sources=refs).model_dump(mode='json'),now=now-case['old_age'])['item']
                if first and not case.get('old_source_available',True):
                    with store.database.transaction() as connection:
                        connection.execute('DELETE FROM naiyue_chat_messages WHERE session_id=? AND message_id=?',('probe','old'))
                jobs.prepare('probe',root,[],model)
                text=case.get('new_text') or case['claim_body'] or '今天暂时试试本地回归，尚未确定长期选择。'
                seed_sources(store,'probe',[dict(id='new',role='user',text=text)],at=now-case['new_age'])
                eid=next(u['evidence_id'] for u in batch()['units'] if u['message_id']=='new')
                p=proposal(text,eid)
                decision=dict(proposal_ids=[case.get('proposal_ref','P1')],relation=case['relation'],
                    target_old_ref=case.get('target_ref','O1') if case['relation'] in ('duplicate','supplement','correction') else None,
                    support=case.get('support','unsupported' if case['relation']=='unsupported' else 'direct'),
                    confirmation_scope=case['confirmation'],claim=None if case['claim_body'] is None else
                    {k:v for k,v in p.items() if k not in ('matter','sources')},new_evidence_ids=[] if case.get('evidence_empty') else [eid],
                    old_evidence_refs=[],reason='固定合成决定',gaps=[])
                model.replies=[json.dumps(dict(overview=dict(goal='回归',progress='已检查',blockers='',next_step=''),proposals=[p])),
                    json.dumps(dict(groups=[dict(group_id='G1',decisions=[decision])]))]
                origin=uuid4().hex
                accepted=await jobs.request(source_session_id='probe',target_session_id='probe',workspace_root=root,
                    origin_run_id=origin,model_id='offline',tool_call_id='evaluation')
                await jobs.after_finish('probe',origin,'completed',model_id='offline')
                await jobs.wait_idle()
                task=await jobs.output(accepted['task_id'],session_id='probe',workspace_root=root)
                rows=store.database.read_rows('SELECT * FROM naiyue_memories',())
                summary=json.loads(task['output'])
                passed=(len(rows)==case['expected_rows'] and model.calls==2
                    and summary['memory_saved']==case['expected_writes']
                    and summary['failed']==case['expected_failed'] and summary['overview_saved']==1)
                if 'expected_statuses' in case:
                    passed=passed and sorted(r['status'] for r in rows)==sorted(case['expected_statuses'])
                if first:
                    row=rows[0]
                    passed=passed and row['memory_id']==first['memory_id'] and row['version']==case['expected_version']
                    passed=passed and (row['expires_at']>first['expires_at'])==case['renewed']
                    if case['confirmation'] in ('partial','unknown'):
                        passed=passed and row['body']==first['body'] and row['expires_at']==first['expires_at']
                        passed=passed and row['evidence_at']==first['evidence_at'] and row['last_confirmed_at']==first['last_confirmed_at']
                sources=[dict(s) for s in store.database.read_rows('SELECT * FROM naiyue_memory_sources',())]
                for row in rows:
                    current=[s for s in sources if s['memory_id']==row['memory_id'] and s['memory_version']==row['version']]
                    expected_body=(first['body'] if first and (not case['expected_writes'] or
                        case['confirmation'] in ('partial','unknown')) else p['body'])
                    passed=passed and row['body']==expected_body
                    expected_messages={'old'} if first else set()
                    if case['expected_writes']:expected_messages.add('new')
                    passed=passed and {s['message_id'] for s in current}==expected_messages
                    for source in current:
                        original=case['old_body'] if source['message_id']=='old' else text
                        passed=passed and source['quote']==original[source['start_offset']:source['end_offset']]
                before=store.database.connection.total_changes
                parts=prepare_memory_parts(memory,'probe',store.sessions.get('probe')['project_id'],
                    [HumanMessage(content='本地回归环境。另外 TC-99。')],None)
                related=[r for part in parts if part['id']=='memory' for r in json.loads(part['text'][part['text'].index('{'):])['related']]
                recalled=next((r['version'] for r in related if first and r['memory_id']==first['memory_id']),None)
                delta=store.database.connection.total_changes-before
                if case['id']=='correction':passed=passed and recalled==2
                return dict(case_id=case['id'],passed=bool(passed and delta==0),logical_calls=model.calls,
                    task=task,rows=[dict(r) for r in rows],sources=sources,recalled_version=recalled,recall_write_delta=delta)
            finally:
                await jobs.aclose();memory.close();await catalog.aclose()


def evaluate(split='calibration'):
    from scripts.evaluate_memory_optimization import evidence_manifest
    from scripts.memory_evaluation_cases import load_engineering_matrix
    cases,checksum,protocol_hash=load_engineering_matrix('worker',split)
    return dict(run_id=uuid4().hex,synthetic=True,provider_calls=0,split=split,
        fixtures_sha256=checksum,protocol_sha256=protocol_hash,contract='engineering_matrix_v1',
        manifest=evidence_manifest(),semantic_status='not_sampled',overall='pending_full_acceptance',
        cases=[asyncio.run(execute(c)) for c in cases])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--split',choices=('calibration','validation','holdout'),default='calibration')
    args=parser.parse_args()
    if args.output.exists() or args.output.is_symlink():parser.error('output must not exist')
    report=evaluate(args.split);write_report(args.output,report)
    print(json.dumps(dict(total=len(report['cases']),passed=sum(c['passed'] for c in report['cases']),provider_calls=0)))
    return int(not all(c['passed'] for c in report['cases']))


if __name__=='__main__':raise SystemExit(main())
