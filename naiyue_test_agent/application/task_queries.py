"""Scoped task views route to their actual Shell or memory owner."""

import hashlib
import json

from langchain_core.tools import ToolException

from .background_tasks import TaskQueryError


class TaskQueries:
    def __init__(self, shell, memory=None):
        self.shell = shell
        self.memory = memory

    async def list(self, *, session_id=None, workspace_root=None, limit=20, before=None):
        scope = dict(session_id=session_id, workspace_root=workspace_root)
        if self.memory is None:
            return await self.shell.list(**scope, limit=limit, before=before)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ToolException('Task list limit must be an integer from 1 to 100.')
        rows, summaries = [], []
        for owner in (self.shell, self.memory):
            page = await owner.list(**scope, limit=100)
            summaries.append(page)
            rows.extend(page['items'])
            while page['next_before'] is not None:
                page = await owner.list(**scope, limit=100, before=page['next_before'])
                if page['revision'] != summaries[-1]['revision']:
                    raise TaskQueryError('task_cursor_invalid', 'Task list changed during pagination.')
                rows.extend(page['items'])
        rows.sort(key=lambda item: (item['created_at'], item['task_id']), reverse=True)
        summary = dict(active_count=sum(page['active_count'] for page in summaries),
            terminal_revision=sum(page['terminal_revision'] for page in summaries),
            revision=hashlib.sha256(json.dumps([page['revision'] for page in summaries]).encode()).hexdigest())
        if before is not None:
            position = next((index for index, item in enumerate(rows) if item['task_id'] == before), None)
            if position is None:
                raise TaskQueryError('task_cursor_invalid', 'Invalid task list cursor.')
            rows = rows[position + 1:]
        page = rows[:limit]
        return dict(summary, items=page, next_before=page[-1]['task_id'] if len(rows) > limit else None)

    async def output(self, task_id, *, session_id=None, workspace_root=None):
        try:
            return await self.shell.output(task_id, session_id=session_id, workspace_root=workspace_root)
        except TaskQueryError as error:
            if error.code != 'task_not_found' or self.memory is None:
                raise
        return await self.memory.output(task_id, session_id=session_id, workspace_root=workspace_root)

    async def stop(self, task_id, *, session_id=None, workspace_root=None):
        try:
            return await self.shell.stop(task_id, session_id=session_id, workspace_root=workspace_root)
        except TaskQueryError as error:
            if error.code != 'task_not_found' or self.memory is None:
                raise
        return await self.memory.stop(task_id, session_id=session_id, workspace_root=workspace_root)
