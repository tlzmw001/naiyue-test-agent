"""Disposable Wiki calibration through production owners; no user databases."""
import asyncio
import json
import logging
import tempfile
import time
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import ToolMessage
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.application.memory.memory_access import MemoryAccess
from naiyue_test_agent.application.memory.memory_jobs import MemoryJobs
from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
from scripts.memory_evaluation_cases import evaluation_model_identity, safe_report, seed_sources


class Observation(BaseCallbackHandler):
    def __init__(self):self.calls=[];self.outputs=[]
    def on_chat_model_start(self,serialized,messages,*,run_id,**kwargs):
        self.calls.append(dict(run_id=str(run_id),start=time.monotonic(),elapsed_ms=None,usage=None))
    def on_llm_end(self,response,*,run_id,**kwargs):
        row=next((r for r in self.calls if r['run_id']==str(run_id)),None)
        message=response.generations[0][0].message
        if row:row.update(elapsed_ms=(time.monotonic()-row['start'])*1000,usage=message.usage_metadata)
        self.outputs.append(message.content)
    def on_llm_error(self,error,*,run_id,**kwargs):
        row=next((r for r in self.calls if r['run_id']==str(run_id)),None)
        if row:row.update(elapsed_ms=(time.monotonic()-row['start'])*1000,error_type=type(error).__name__)


def seed_wiki(access,sid,source,now):
    """Frozen prevalidated baseline, with real archive references for every old row."""
    shift=now-source['now'];pages={};saved=access.storage.sessions.get(sid);workspace=saved['project_id'];thread=saved['thread_id']
    for item in source['wiki']['pages']:
        page=WikiWriter.new_page(item['topic'],item['title'],item.get('description',''))
        pages[item['page_id']]=(item['scope'],page)
    for number,item in enumerate(source['wiki']['memories']):
        scope,page=pages[item['page_id']];repo=access._stores(scope)[0][1]
        stamp=item.get('evidence_at',item['expires_at']-30*86400)+shift
        mid=f'old-{number}'
        seed_sources(access.storage,sid,[dict(id=mid,role='user',text=item['body'])],at=stamp)
        with access.storage.memory.batch_reader(__import__('re').compile(r'(?!)')) as archive:
            # Source resolver validates the actual saved message and its timestamp.
            from naiyue_test_agent.application.memory.memory_sources import resolve_wiki
            reference=dict(current_user=False,session_id=sid,thread_id=thread,message_id=mid,revision=1,conversation_revision=0,
                quote=item['body'][:300],offset=0)
            verified=resolve_wiki(archive,workspace,sid,[reference],[0] if scope=='personal' else [],now)
        row=dict(memory_id=uuid4().hex,version=item['version'],scope=scope,workspace_id=workspace if scope=='workspace' else None,
            page_id=page['page_id'],status=item['status'],title=page['title'],body=item['body'],aliases_json='[]',tags_json='[]',
            machine=None,module=None,target=None,subject=page['title'],basis='user_declaration',created_at=stamp,updated_at=stamp,
            evidence_at=stamp,last_confirmed_at=stamp,ttl_days=30,expires_at=item['expires_at']+shift,gaps_json='[]',reason='',deadline_at=None)
        with repo.wiki_edit(scope,workspace if scope=='workspace' else None,now=now,capacity=30) as writer:
            try:old=writer.page(page['page_id'])
            except Exception as error:
                from naiyue_test_agent.contracts.memory import MemoryReadError
                if not isinstance(error,MemoryReadError) or str(error)!='memory_not_found':raise
                old=page
            else:old=dict(old,expected_version=old['version'])
            writer.apply(pages=[old],memories=[dict(row=row,sources=verified,expected_version=None)])
    if not source['scenario']['source_available']:
        with access.storage.database.transaction(write=True) as connection:
            connection.execute('DELETE FROM naiyue_chat_messages WHERE session_id=?', (sid,))
    return pages


async def background_case(case,catalog,model_id,settings=None):
    from naiyue_test_agent.workspaces import prepare_workspace

    started=time.monotonic();source=case['input'];now=time.time();shift=now-source['now']
    result=dict(case_id=case['case_id'],status='failed',observations={},semantic_review=None)
    with tempfile.TemporaryDirectory(prefix='nyt-wiki-eval-') as temp:
        root=Path(temp).resolve()
        location = prepare_workspace(root, data_dir=root/'data', database_path=root/'state.sqlite')
        workspace = location.workspace_id
        with ProjectStore(root/'state.sqlite') as store:
            store.sessions.bind_workspace(workspace,root);store.memory.prepare_wiki(now=now)
            for sid in ('old','probe'):
                store.sessions.create(sid,dict(thread_id=sid,model_id=model_id,workspace_root=root,active_tool_names=['memory'],permission_mode='Default'))
            settings=(settings or MemorySettings()).model_copy(update={'auto_enabled':False})
            access=MemoryAccess(store,settings,root/'data');jobs=MemoryJobs(access,catalog)
            try:
                seed_wiki(access,'old',source,now)
                for i,entry in enumerate(source['sources']):
                    material=dict(id=f'new-{i}',role=entry['role'],text=entry['text'])
                    if entry['role']=='tool':material['tool_call_id']=f'new-tool-{i}'
                    seed_sources(store,'probe',[material],at=entry['occurred_at']+shift)
                async def injected(*a,**kw):raise OSError('synthetic_B_failure')
                fault=patch('naiyue_test_agent.application.memory.memory_organization.organize',injected) if source['scenario']['failure_stage']=='B' else nullcontext()
                with fault:
                    accepted=await jobs.request(source_session_id='probe',target_session_id='probe',workspace_root=root,
                        origin_run_id='evaluation',model_id=model_id)
                    await jobs.after_finish('probe','evaluation','completed',model_id=model_id)
                    await jobs.wait_idle()
                job=jobs._entries[accepted['task_id']]
                memories = _memory_snapshot(access)
                sources = _source_snapshot(access, memories)
                source_keys = {(proof['scope'], proof['memory_id'], proof['memory_version']) for proof in sources}
                missing_proof = any((row['scope'], row['memory_id'], row['version']) not in source_keys
                                    for row in memories if row['status'] in ('active', 'candidate'))
                injected_ok=source['scenario']['failure_stage']=='B' and job.overview_saved==1 and job.saved==0 and job.failed>0
                completed = (job.result_status == 'completed' or injected_ok) and not missing_proof
                result.update(status='completed' if completed else 'failed',observations=dict(
                    result_status=job.result_status,overview_saved=job.overview_saved,saved=job.saved,
                    failures=job.failed,error_type=job.error_type,termination=job.termination,skipped=job.skipped,memories=memories,complete=True,
                    injected_failure=source['scenario']['failure_stage'], sources=sources))
                if missing_proof:
                    result['observations']['failure_reason'] = 'memory_source_projection_missing'
            except Exception as error:result['observations']=dict(error_type=type(error).__name__,complete=False)
            finally:
                await jobs.aclose();access.close()
        question = source['scenario'].get('followup')
        if not question and source['scenario'].get('navigation_target'):
            question = '\n'.join(entry['text'] for entry in source['sources'])
        if question and result['status'] == 'completed':
            observed = result['observations']
            followup = observed['followup'] = {'status': 'failed'}
            try:
                async with _open_eval_application(root, root/'state.sqlite', workspace, catalog, settings,
                                                  read_only=True) as app:
                    sid = await app.host.sessions.create_workspace(model_id, root)
                    await _foreground_turn(app, sid, question, catalog.get(model_id), followup)
                    after = _memory_snapshot(app.host.memory._access)
                    unchanged = (after == observed['memories']
                        and _source_snapshot(app.host.memory._access, after) == observed['sources'])
                    passed = unchanged and not any(item['status'] == 'error' for item in followup['tool_feedback'])
                    followup.update(status='completed' if passed else 'failed', memory_unchanged=unchanged)
                    if not passed:
                        result['status'] = 'failed'
            except Exception as error:
                followup.update(status='failed', error_type=type(error).__name__)
                result['status'] = 'failed'
    result['elapsed_ms']=(time.monotonic()-started)*1000
    return result


def _memory_snapshot(access):
    return [row for _, repo in access._stores('both') for row in repo.database.read_rows(
        'SELECT memory_id,page_id,version,scope,status,title,body,evidence_at,expires_at FROM naiyue_memories ORDER BY memory_id', ())]


def _source_snapshot(access, memories):
    result = []
    fields = ('memory_id', 'memory_version', 'session_id', 'message_id', 'start_offset',
              'end_offset', 'quote', 'supports_scope')
    for scope, repo in access._stores('both'):
        versions = [(row['memory_id'], row['version']) for row in memories if row['scope'] == scope]
        for start in range(0, len(versions), 50):
            for proof in repo.wiki_sources(versions[start:start+50]):
                result.append(dict(scope=scope, **{field: proof[field] for field in fields}))
    return sorted(result, key=lambda proof: (proof['scope'], proof['memory_id'], proof['message_id'],
                                             proof['start_offset']))


class _BorrowedCatalog:
    """The batch owns the catalog; a disposable case owns only its application resources."""

    def __init__(self, catalog):
        self._catalog = catalog

    def __getattr__(self, name):
        return getattr(self._catalog, name)

    async def aclose(self):
        # The batch closes the SDK clients after all cases finish.
        return None


@asynccontextmanager
async def _open_eval_application(root, path, workspace, catalog, settings, *, read_only=False):
    from naiyue_test_agent import application
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools.history import create_history_tool
    from naiyue_test_agent.tools.memory import create_memory_tool
    from scripts.component_fixtures import open_component_application

    denied = ['organize', 'refresh_overview']
    if read_only:
        denied.extend(('record', 'update', 'disable', 'forget'))
    config = AppSettings.model_validate(dict(storage=dict(data_dir=str(root/'data')),
        memory=settings.model_copy(update={'auto_enabled':False}).model_dump(),
        permissions=dict(deny=[dict(tool='history'), *[
            dict(tool='memory', args=dict(action=action)) for action in denied]])))
    prompt = ('根据已有 Wiki 记忆回答。需要时按导航读取。' if read_only else
        '根据真实用户陈述维护适合长期保留的记忆。需要时按Wiki导航读取。不要把任务进度当长期记忆。失败如实说明，不重试。')
    with patch.object(application, 'load_model_catalog', lambda _: _BorrowedCatalog(catalog)):
        async with open_component_application('injected', database_path=path,
                tools=[create_memory_tool(), create_history_tool()],
                initial_active_tools=['memory'], settings=config, response_format=None, system_prompt=prompt,
                workspace_binding=dict(workspace_id=workspace, root_path=root)) as app:
            yield app


async def _foreground_turn(app, sid, prompt, model, observed):
    feedback = {}
    calls = 0

    def remember(message):
        if isinstance(message, ToolMessage):
            feedback[message.tool_call_id] = dict(name=message.name, tool_call_id=message.tool_call_id,
                status=message.status, content=safe_report(message.content))

    class TurnObservation(BaseCallbackHandler):
        def on_chat_model_start(self, serialized, messages, **kwargs):
            nonlocal calls
            calls += 1
            for batch in messages:
                for message in batch:
                    remember(message)

        def on_tool_end(self, output, **kwargs):
            remember(output)

    previous = model.callbacks
    model.callbacks = [*(getattr(previous, 'handlers', previous) or []), TurnObservation()]
    try:
        reply = await app.host.runs.start(sid, [('user', prompt)])
        for message in reply['messages']:
            remember(message)
        observed['answer'] = safe_report(reply['messages'][-1].content)
    finally:
        model.callbacks = previous
        observed.update(tool_feedback=list(feedback.values()), logical_calls=calls)


async def foreground_case(case,catalog,model_id,settings=None):
    from naiyue_test_agent.workspaces import prepare_workspace
    source=case['input'];started=time.monotonic()
    result=dict(case_id=case['case_id'],status='failed',observations={},semantic_review=None)
    if any(s['role']!='user' for s in source['sources']):
        result['observations']=dict(reason='foreground_nonuser_source_requires_tool_execution');return result
    with tempfile.TemporaryDirectory(prefix='nyt-wiki-foreground-') as tmp:
        root=Path(tmp).resolve();location=prepare_workspace(root,data_dir=root/'data',database_path=root/'state.sqlite')
        async with _open_eval_application(root, root/'state.sqlite', location.workspace_id,
                                          catalog, settings or MemorySettings()) as app:
            sid=await app.host.sessions.create_workspace(model_id,root)
            old=await app.host.sessions.create_workspace(model_id,root)
            access=app.host.sessions._persistence.sessions.memory._access
            observations=result['observations']
            stage='seed'
            try:
                seed_wiki(access,old,source,time.time())
                observations['baseline_memories']=_memory_snapshot(access)
                model = catalog.get(model_id)
                stage='foreground_turn'
                await _foreground_turn(app, sid, '\n'.join(s['text'] for s in source['sources']), model, observations)
                stage='snapshot'
                observations['memories']=_memory_snapshot(access)
                observations['sources']=_source_snapshot(access, observations['memories'])
                if source['scenario'].get('followup'):
                    stage='followup'
                    followup_sid=await app.host.sessions.create_workspace(model_id,root)
                    followup = observations['followup'] = {}
                    await _foreground_turn(app, followup_sid,
                        '只读取需要的资料并回答，不修改任何记忆。\n'+source['scenario']['followup'], model, followup)
                    after=_memory_snapshot(access)
                    unchanged=(after == observations['memories']
                               and _source_snapshot(access, after) == observations['sources'])
                    observations['followup'].update(
                        status='completed' if unchanged else 'failed', memory_unchanged=unchanged)
                    if not unchanged:
                        observations.update(memories_after_followup=after, failure_reason='followup_mutated_memory', complete=False)
                        return result
                result['status']='completed'
                observations['complete']=True
            except Exception as error:
                observations.update(error_type=type(error).__name__, stage=stage, complete=False)
                if stage == 'foreground_turn':
                    try:
                        observations['memories'] = _memory_snapshot(access)
                    except Exception as snapshot_error:
                        observations['snapshot_error'] = type(snapshot_error).__name__
                if stage == 'followup' and 'followup' in observations:
                    observations['followup']['status'] = 'failed'
    result['elapsed_ms']=(time.monotonic()-started)*1000
    return result


async def run(args):
    import os
    previous=logging.root.manager.disable
    environment={k:os.environ.get(k) for k in ('LANGSMITH_TRACING','LANGCHAIN_TRACING_V2')}
    logging.disable(logging.CRITICAL)
    for key in environment:os.environ[key]='false'
    try:return await _run(args)
    finally:
        logging.disable(previous)
        for key,value in environment.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


async def _run(args):
    from naiyue_test_agent.model_access import load_model_catalog
    from naiyue_test_agent.settings import load_settings
    from scripts.memory_wiki_evaluation import load_suite,provenance
    from scripts.memory_evaluation_cases import write_report
    cases,_=load_suite();cases=[c for c in cases if c['split']==args.split]
    catalog = load_model_catalog(args.config)
    model = None
    try:
        settings = load_settings().memory
        identity=args.model_id or settings.model_id or catalog.default_model_id
        catalog.prepare_async_clients();model=catalog.get(identity);observer=Observation();previous=model.callbacks
        model.callbacks=[*(previous or []),observer]
        report=dict(effective_model=evaluation_model_identity(model,settings),protocol='wiki_v1',split=args.split,mode=args.mode,provenance=provenance(),model_id=identity,
            runtime_status='background_and_foreground_connected',product_quality_passed=None,semantic_status='needs_review',cases=[],sdk_attempts=None,fees=None)
        args.output_dir.mkdir(parents=True,exist_ok=True)
        import subprocess,zipfile
        paths=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard','--','naiyue_test_agent','scripts','tests']).decode().split('\0')
        with zipfile.ZipFile(args.output_dir/'source.zip','x',zipfile.ZIP_DEFLATED) as frozen:
            for path in sorted(set(paths)-{''}):
                if Path(path).is_file():frozen.write(path)
    except BaseException:
        if model is not None:
            model.callbacks = previous
        try:
            await catalog.aclose()
        except Exception as cleanup_error:
            logging.getLogger(__name__).warning('wiki_eval_preflight_cleanup_failed error_type=%s',
                                                type(cleanup_error).__name__)
        raise
    try:
        for case in cases:
            try:
                if args.mode not in case['input']['scenario']['execution_modes']:
                    value=dict(case_id=case['case_id'],status='skipped',observations=dict(reason='mode_not_applicable'),semantic_review=None)
                elif args.mode=='foreground':value=await foreground_case(case,catalog,identity,settings)
                else:value=await background_case(case,catalog,identity,settings)
            except Exception as error:
                value=dict(case_id=case['case_id'],status='failed',observations=dict(error_type=type(error).__name__,stage='case_setup'),semantic_review=None)
            report['cases'].append(value);report['model_requests']=len(observer.calls)
            report['model_calls']=[{k:v for k,v in r.items() if k!='start'} for r in observer.calls]
            report['model_outputs']=observer.outputs
            write_report(args.output_dir/(case['case_id']+'.json'),safe_report(value))
            print(json.dumps(dict(case_id=value['case_id'],status=value['status'],model_requests=len(observer.calls))),flush=True)
    finally:
        model.callbacks=previous
        try:await catalog.aclose()
        except Exception as error:report['cleanup_error']=type(error).__name__
    write_report(args.output_dir/'report.json',safe_report(report))
    return report
