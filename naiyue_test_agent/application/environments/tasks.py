"""Bound sandbox task projections never borrow the Host task manager or paths."""
import base64
import hashlib
import json
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.application.sessions.background_tasks import TaskQueryError
from naiyue_test_agent.tools.output_text import OutputAccumulator
from naiyue_test_agent.tools.settings import ProcessToolSettings


class SandboxTasks:
    def __init__(self,coordinator,record,owner):
        self.coordinator,self.record,self.owner=coordinator,record,owner

    def _scope(self,session_id,workspace_root):
        if session_id not in (None,self.record.session_id) or workspace_root is not None:
            raise TaskQueryError('task_not_found','Task scope differs from its execution binding.')

    @staticmethod
    def _public(row):
        result=row.get('result') or {};outcome=result.get('value') or {}
        status='failed' if row['state']=='completed' and outcome.get('returncode') not in (0,None) else row['state']
        return dict(kind='shell',task_id=row['operation_id'],session_id=row['session_id'],environment_binding=row['binding_id'],
            status=status,stale=row['stale'],state_is_last_confirmed=True,created_at=row['created_at'],
            command_preview=row['metadata'].get('command_preview',''),observed_raw_bytes=row['observed_bytes'],
            exit_code=outcome.get('returncode'),termination=outcome.get('termination'),error_code=row.get('error_code'),
            output_complete=bool(outcome.get('output_complete') and not row['metadata'].get('gap') and
                not row['metadata'].get('truncated') and row['metadata'].get('offset',0)>=row['observed_bytes']),
            cleanup_confirmed=all(outcome.get(key) for key in ('process_reaped','pipes_closed','consumer_settled')))

    async def list(self,*,session_id=None,workspace_root=None,limit=20,before=None):
        self._scope(session_id,workspace_root)
        if type(limit) is not int or not 1<=limit<=100:raise TaskQueryError('task_cursor_invalid','Invalid task list limit.')
        with self.owner.operation():
            rows=await run_blocking(lambda:self.coordinator.records().list(self.record.binding_id))
        rows=[row for row in reversed(rows) if row['kind'] in ('shell','argv')]
        revision=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
        if before is not None:
            index=next((index for index,row in enumerate(rows) if row['operation_id']==before),None)
            if index is None:raise TaskQueryError('task_cursor_invalid','Invalid task list cursor.')
            rows=rows[index+1:]
        page=rows[:limit]
        return dict(items=[self._public(row) for row in page],next_before=page[-1]['operation_id'] if len(rows)>limit else None,
            active_count=sum(row['state'] in ('starting','running','submitting','unknown') for row in rows),
            terminal_revision=sum(row['state'] in ('completed','failed','stopped') for row in rows),revision=revision)

    async def _get(self,ident,stop):
        with self.owner.operation():
            try:
                existing=await run_blocking(lambda:self.coordinator.records().get(self.record.binding_id,ident))
                if existing['kind'] not in ('shell','argv'):raise TaskQueryError('task_not_found','This operation is not a Shell task.')
                row=await run_blocking(self.coordinator.stop if stop else self.coordinator.refresh,self.record.binding_id,ident)
            except SandboxError as error:
                if error.code=='env_operation_not_found':raise TaskQueryError('task_not_found','Task does not exist in this session.') from error
                raise
        if row['kind'] not in ('shell','argv'):raise TaskQueryError('task_not_found','This operation is not a Shell task.')
        if not self.coordinator._closing and not row['stale'] and row['state'] in ('starting','running'):
            self.coordinator.watch(self.record.binding_id,ident,self.record.settings['poll_milliseconds'])
        return row

    async def output(self,task_id,*,session_id=None,workspace_root=None):
        self._scope(session_id,workspace_root)
        row=await self._get(task_id,False);meta=row['metadata']
        settings=ProcessToolSettings.model_validate({key:value for key,value in meta['settings'].items() if key in ProcessToolSettings.model_fields})
        output=OutputAccumulator(settings);output.feed(base64.b64decode(meta.get('tail_base64','')));output.finish()
        return dict(self._public(row),output=output.text,output_is_tail=True,output_available=True,
            truncated=bool(meta.get('truncated') or meta.get('gap') or output.truncated),lossy=output.lossy,observed_text_bytes=output.observed_bytes,archive_paths={'raw':meta['archive_path']} if meta.get('archive_path') else {},
            archive_saved_bytes=meta.get('saved_bytes',0),archive_complete=self.coordinator.finished(row) and not(meta.get('gap') or meta.get('truncated')),
            archive_reason='capture_gap' if meta.get('gap') else 'command_limit' if meta.get('truncated') else None)

    async def stop(self,task_id,*,session_id=None,workspace_root=None):
        self._scope(session_id,workspace_root)
        return self._public(await self._get(task_id,True))
