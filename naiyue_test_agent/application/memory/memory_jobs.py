"""Application-owned memory queue; root completion releases accepted targets."""

import asyncio
from contextvars import Context
from contextlib import contextmanager
from dataclasses import dataclass,field
from datetime import datetime,timezone
import hashlib
import json
import logging
from threading import Lock
import time
from uuid import uuid4

from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.agent_runtime.events import RunEventPublisher
from naiyue_test_agent.agent_runtime.run_control import RunControl
from naiyue_test_agent.tools.permission_rules import match_rules
from naiyue_test_agent.application.sessions.background_tasks import TaskQueryError
from .memory_queries import MemoryReadError
from .memory_context import prepare_memory_parts
from naiyue_test_agent.application.observability.run_events import _log_event


logger=logging.getLogger(__name__)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MemoryJob:
    task_id:str
    session_id:str
    target_session_id:str
    root:object
    origin_run_id:str
    tool_call_id:str
    model_id:str
    model:object
    thread_id:str
    conversation_revision:int
    project_id:str|None=None
    created_at:str=field(default_factory=timestamp)
    deadline:float=0.
    status:str='starting'
    result_status:str|None=None
    started_at:str|None=None
    finished_at:str|None=None
    termination:str|None=None
    error_type:str|None=None
    cleanup_confirmed:bool=False
    released:bool=False
    start:tuple=(0,0)
    upper:int=0
    requested_start:int|None=None
    requested_end:int|None=None
    batches:int=0
    saved:int=0
    failed:int=0
    overview_saved:int=0
    skipped:list=field(default_factory=list)
    control:RunControl=field(default_factory=RunControl)
    worker:object=None
    pending_runs:set=field(default_factory=set)
    changed:asyncio.Event=field(default_factory=asyncio.Event)
    terminal_revision:int=0


class MemoryJobs:
    def __init__(self,memory,catalog,*,publisher_factory=None,permission_settings=None,flush_usage=None):
        self.memory,self.storage,self.settings,self.catalog=memory,memory.storage,memory.settings,catalog
        if self.settings.model_id is not None and self.settings.model_id not in catalog.model_ids:
            raise ValueError('memory_model_unavailable')
        self._publisher=publisher_factory or (lambda sid,rid:RunEventPublisher(sid,rid,(_log_event,)))
        self._permissions=permission_settings
        self._flush_usage=flush_usage
        self._entries={};self._targets={};self._closing=False;self._terminal_sequence=0
        self._deleting=set()
        self._deletion_generations={}
        self._slots=asyncio.Semaphore(self.settings.max_active_tasks)
        self._eligible=set();self._used=set();self._context_lock=Lock()

    def allowed(self,request):
        return not self._permissions or match_rules(self._permissions.deny,dict(tool_name='memory',args=request),deny=True) is None

    def _enabled_for_session(self,sid):
        row=self.storage.sessions.get(sid)
        binding=self.storage.sessions.get_workspace_binding()
        return bool(binding and row['project_id']==binding['workspace_id'] and
            'memory' in json.loads(row['active_tool_names_json']) and self.allowed(dict(action='list')))

    def prepare(self,sid,root,messages,model,*,preview=False):
        if not self.settings.enabled or not self._enabled_for_session(sid):return []
        row=self.storage.sessions.get(sid)
        if str(root)!=row['workspace_root']:raise MemoryReadError('memory_scope_mismatch')
        if preview:
            progress=self.storage.memory.progress(sid)
            seen=bool(progress and progress[0]['directory_seen'])
        else:
            progress=self.storage.memory.ensure(sid);seen=bool(progress['directory_seen'])
            with self._context_lock:self._eligible.add(sid)
        with self._context_lock:seen|=sid in self._used
        directory=self.storage.memory.directory(sid,self.settings.directory_limit) if not seen else ()
        return prepare_memory_parts(self.memory,sid,row['project_id'],messages,model,directory=directory,allowed=self.allowed)

    def directory_attempt(self,sid):
        with self._context_lock:
            if sid in self._eligible:self._used.add(sid)

    def _selected(self,model_id,model=None):
        selected=self.settings.model_id or model_id
        return selected,model if model is not None and selected==model_id else self.catalog.get(selected)

    async def request(self,*,source_session_id,target_session_id,workspace_root,origin_run_id,model_id,
                      tool_call_id=None,model=None,start_sequence=None,end_sequence=None):
        if self._closing or {source_session_id,target_session_id}&self._deleting:
            raise MemoryReadError('memory_admission_closed')
        generations={sid:self._deletion_generations.get(sid,0) for sid in (source_session_id,target_session_id)}
        if not self.allowed(dict(action='refresh_overview')):raise MemoryReadError('memory_permission_denied')
        row=await run_blocking(self.storage.sessions.get,target_session_id)
        caller=await run_blocking(self.storage.sessions.get,source_session_id)
        binding=await run_blocking(self.storage.sessions.get_workspace_binding)
        if (self._closing or {source_session_id,target_session_id}&self._deleting
                or any(self._deletion_generations.get(sid,0)!=value for sid,value in generations.items())):
            raise MemoryReadError('memory_admission_closed')
        if (not binding or row['project_id']!=binding['workspace_id'] or caller['project_id']!=binding['workspace_id']
                or caller['workspace_root']!=str(workspace_root)):
            raise MemoryReadError('memory_scope_mismatch')
        old=self._entries.get(self._targets.get(target_session_id))
        if old and not old.cleanup_confirmed:
            if old.thread_id!=row['thread_id'] or old.conversation_revision!=row['conversation_revision']:
                raise MemoryReadError('memory_source_changed')
            # A pending root request never prematurely releases a previous caller's target.
            if old.session_id!=source_session_id:raise MemoryReadError('memory_target_pending')
            if (start_sequence is not None or end_sequence is not None
                    or old.requested_start is not None or old.requested_end is not None):
                raise MemoryReadError('memory_target_pending')
            old.pending_runs.add((source_session_id,origin_run_id))
            return dict(status='accepted',task_id=old.task_id,merged=True)
        pending=sum(not j.cleanup_confirmed and j.status=='starting' for j in self._entries.values())
        if pending>=self.settings.max_pending_tasks:raise MemoryReadError('memory_queue_full')
        selected,model=self._selected(model_id,model)
        job=MemoryJob(uuid4().hex,source_session_id,target_session_id,workspace_root,origin_run_id,
            tool_call_id or '',selected,model,row['thread_id'],row['conversation_revision'],
            project_id=binding['workspace_id'],deadline=time.monotonic()+self.settings.task_timeout_seconds,
            requested_start=start_sequence,requested_end=end_sequence)
        job.pending_runs.add((source_session_id,origin_run_id))
        self._entries[job.task_id]=job;self._targets[target_session_id]=job.task_id
        job.worker=asyncio.create_task(self._drive(job),context=Context())
        return dict(status='accepted',task_id=job.task_id,merged=False)

    async def after_finish(self,sid,run_id,status,*,model_id,model=None):
        with self._context_lock:used=sid in self._used
        if used:
            try:await run_blocking(self.storage.memory.mark_directory, sid)
            except Exception as error:logger.warning('memory_directory_marker_failed error_type=%s',type(error).__name__)
        # Approval resumes have a new run ID but still complete this caller's same logical turn.
        pending=[j for j in self._entries.values() if any(owner==sid for owner,_ in j.pending_runs) and not j.cleanup_confirmed]
        if status in ('interrupted','skipped') and not self._closing:return
        if status!='completed' or self._closing:
            for job in pending:
                job.pending_runs={key for key in job.pending_runs if key[0]!=sid};job.changed.set()
                if not job.released and not job.pending_runs:
                    job.termination='root_not_completed';job.control.request('failure')
            return
        if not pending and not self._enabled_for_session(sid):return
        progress=await run_blocking(self.storage.memory.tick, sid)
        if self.settings.auto_enabled and self._enabled_for_session(sid) and (progress['completed_turns']-progress['accepted_turns']>=self.settings.trigger_turns
                or progress['completed_chars']-progress['accepted_chars']>=self.settings.trigger_chars):
            row=await run_blocking(self.storage.sessions.get,sid)
            try:
                from pathlib import Path
                result=await self.request(source_session_id=sid,target_session_id=sid,workspace_root=Path(row['workspace_root']),
                    origin_run_id=run_id,model_id=model_id,model=model)
                job=self._entries[result['task_id']]
                if job not in pending:pending.append(job)
            except MemoryReadError as error:
                logger.warning('memory_registration_failed code=%s',error)
        for job in pending:
            try:await self._release(job)
            except Exception as error:
                job.error_type=type(error).__name__;job.termination='admission_save_failed'
                logger.error('memory_admission_failed error_type=%s',type(error).__name__)
                job.control.request('failure')
            finally:
                job.pending_runs={key for key in job.pending_runs if key[0]!=sid};job.changed.set()

    async def _release(self,job):
        if job.cleanup_confirmed:return
        progress=await run_blocking(self.storage.memory.ensure, job.target_session_id)
        row=await run_blocking(self.storage.sessions.get,job.target_session_id)
        upper_rows=await run_blocking(self.storage.conversation.upper_sequence, job.target_session_id, job.thread_id)
        upper=upper_rows
        if (row['thread_id']!=job.thread_id or row['conversation_revision']!=job.conversation_revision
                or job.requested_end is not None and job.requested_end>upper):
            job.termination='source_changed';job.control.request('failure');return
        upper=min(upper,job.requested_end) if job.requested_end is not None else upper
        start=(job.requested_start if job.requested_start is not None else progress['next_sequence'],progress['next_offset'])
        await run_blocking(self.storage.memory.accept, job.target_session_id,thread_id=job.thread_id,
            conversation_revision=job.conversation_revision,upper=upper)
        if not job.released:job.start=start
        job.upper=max(job.upper,upper);job.released=True
        job.changed.set()

    async def _drive(self,job):
        from .memory_job_runner import drive
        try:await drive(self,job)
        finally:
            if self._flush_usage is not None:
                try:await self._flush_usage(job.session_id,job.task_id)
                except Exception as error:logger.error('memory_usage_flush_failed error_type=%s',type(error).__name__)

    def _terminal(self,job,result,reason):
        job.status='finished';job.result_status=result;job.termination=reason
        job.finished_at=timestamp();job.cleanup_confirmed=True
        self._terminal_sequence+=1;job.terminal_revision=self._terminal_sequence

    def _public(self,job):
        return dict(task_id=job.task_id,session_id=job.session_id,workspace_root=str(job.root),origin_run_id=job.origin_run_id,
            tool_call_id=job.tool_call_id,kind='memory',shell='memory',command_preview='整理会话概览与记忆',
            status=job.status,result_status=job.result_status,created_at=job.created_at,started_at=job.started_at,
            finished_at=job.finished_at,max_execution_seconds=self.settings.task_timeout_seconds,exit_code=None,
            termination=job.termination,output_complete=job.cleanup_confirmed,cleanup_confirmed=job.cleanup_confirmed,error_type=job.error_type)

    def _find(self,task_id,sid,root):
        job=self._entries.get(task_id)
        if job is None or sid is not None and job.session_id!=sid or root is not None and job.root!=root:
            raise TaskQueryError('task_not_found','Memory task not found.')
        return job

    async def list(self,*,session_id=None,workspace_root=None,limit=20,before=None):
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid task limit')
        jobs=[j for j in reversed(tuple(self._entries.values())) if (session_id is None or j.session_id==session_id)
            and (workspace_root is None or j.root==workspace_root)]
        terminal=max((j.terminal_revision for j in jobs),default=0)
        metadata=[self._public(j) for j in jobs]
        revision=hashlib.sha256(json.dumps(metadata,sort_keys=True).encode()).hexdigest()
        active=sum(not j.cleanup_confirmed for j in jobs)
        if before is not None:
            index=next((i for i,j in enumerate(jobs) if j.task_id==before),None)
            if index is None:raise TaskQueryError('task_cursor_invalid','Invalid task cursor.')
            jobs=jobs[index+1:]
        page=jobs[:limit]
        return dict(items=[self._public(j) for j in page],next_before=page[-1].task_id if len(jobs)>limit else None,
            active_count=active,terminal_revision=terminal,revision=revision)

    async def output(self,task_id,*,session_id=None,workspace_root=None):
        job=self._find(task_id,session_id,workspace_root)
        output=json.dumps(dict(batches=job.batches,overview_saved=job.overview_saved,memory_saved=job.saved,
            failed=job.failed,skipped=job.skipped[:32],result_status=job.result_status),ensure_ascii=False)
        return self._public(job)|dict(output=output,truncated=len(job.skipped)>32,observed_raw_bytes=len(output.encode()),
            observed_text_bytes=len(output.encode()),lossy=False,archive_paths={},archive_saved_bytes=0,
            archive_complete=False,archive_reason='not_applicable',output_available=True)

    async def stop(self,task_id,*,session_id=None,workspace_root=None):
        job=self._find(task_id,session_id,workspace_root)
        if not job.cleanup_confirmed:
            job.control.request();job.changed.set();job.status='stopping'
            if job.worker is None:self._terminal(job,'stopped','user_stop')
            else:await await_settled(job.worker)
        return self._public(job)

    async def wait_idle(self):
        await asyncio.gather(*(j.worker for j in self._entries.values() if j.worker is not None))

    @contextmanager
    def deletion_guard(self,sid):
        if sid in self._deleting or any(not j.cleanup_confirmed and sid in (j.session_id,j.target_session_id) for j in self._entries.values()):
            from langchain_core.tools import ToolException
            raise ToolException('Session has active memory tasks.')
        self._deleting.add(sid)
        self._deletion_generations[sid]=self._deletion_generations.get(sid,0)+1
        try:yield
        finally:self._deleting.discard(sid)

    def forget_session(self,sid):
        for key,job in tuple(self._entries.items()):
            if sid in (job.session_id,job.target_session_id):self._entries.pop(key)
        self._targets.pop(sid,None)

    async def aclose(self):
        self._closing=True
        for job in self._entries.values():
            if not job.cleanup_confirmed:
                job.control.request('close');job.changed.set()
                if job.worker is None:self._terminal(job,'stopped','application_close')
        await self.wait_idle()
