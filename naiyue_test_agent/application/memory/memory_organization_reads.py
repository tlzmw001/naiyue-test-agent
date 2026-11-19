"""Task-local authorized discovery and complete-reading ledger, with no model IO."""
import json
import time
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.tools.memory_wiki_actions import compile_wiki_request
from .memory_wiki_reads import query
from .memory_wiki_render import fit


class Ledger:
    def __init__(self,scope,topic,*,writable=None):
        self.scope,self.topic,self.writable=scope,topic,writable
        self.directories=set();self.pages={};self.old={};self.discovered=set(writable or ())
        self.states=None;self.expiry=None;self.pending={};self.seen=set();self.touched=set(writable or ())

    def read(self,access,caller,project_id,raw,allowed):
        request=compile_wiki_request({'request':{'scope':self.scope,**raw}})
        if request['scope']!=self.scope:raise MemoryReadError('memory_scope_mismatch')
        if not allowed(request):raise MemoryReadError('memory_permission_denied')
        key=json.dumps(request,sort_keys=True)
        if key in self.seen:raise MemoryReadError('memory_read_no_progress')
        self.seen.add(key)
        with access._lock:
            namespace=access._binding(caller,project_id,self.scope,request['action'])
            repo=access._stores(self.scope)[0][1]
            view=repo.wiki_view(self.scope,namespace if self.scope=='workspace' else None,
                dict(action='list_topics'),now=time.time(),include_candidates=True)
            state=[view['generation_id'],view['catalog_revision']]
            if self.states is not None and state!=self.states:raise MemoryReadError('memory_cursor_stale')
            self.states=state
            if view['expires_at'] is not None:self.expiry=min(self.expiry or view['expires_at'],view['expires_at'])
            if request['action']=='read_page':
                pid=request['page_id']
                if pid not in self.discovered:raise MemoryReadError('memory_page_not_discovered')
                self.touched.add(pid)
            if request['action']=='read':
                if not any(o['memory_id']==request['memory_id'] for o in self.old.values()):
                    raise MemoryReadError('memory_not_discovered')
                # Explicit source/history reads use the same bounded public path.
                return access.query_wiki(caller,project_id,request)
            if request['action']=='list_pages' and self.topic is not None and request['topic']!=self.topic:
                raise MemoryReadError('memory_topic_invalid')
            projection=(request['action'],request.get('topic') or request.get('page_id'))
            expected=self.pending.get(projection)
            if request.get('cursor')!=expected:raise MemoryReadError('memory_read_incomplete')
            result=query(access,namespace,caller,request,time.time(),include_candidates=True,
                max_tokens=max(256,access.settings.wiki_read_tokens-400),allows_link=allowed)
            payload=json.loads(result['content'][8:-4])
            if request['action']=='list_pages':
                self.discovered.update(p['page_id'] for p in payload['pages'])
                if result['complete']:self.directories.add(request['topic'])
            else:
                pid=request['page_id'];page=payload['page']
                entry=self.pages.setdefault(pid,dict(page=page,entries={},complete=False))
                if entry['page']['version']!=page['version']:raise MemoryReadError('memory_version_changed')
                for row in payload['entries']:
                    old=entry['entries'].setdefault(row['memory_id'],dict(row,body=''))
                    if len(old['body'])!=row['offset'] or old['version']!=row['version']:
                        raise MemoryReadError('memory_read_incomplete')
                    old['body']+=row['body'];old['entry_complete']=row['entry_complete']
                    if row['entry_complete'] and not any(o['memory_id']==row['memory_id'] for o in self.old.values()):
                        self.old[f'O{len(self.old)+1}']=dict(old,page_id=pid,scope=self.scope)
                if result['complete']:
                    if any(not r['entry_complete'] for r in entry['entries'].values()):raise MemoryReadError('memory_read_incomplete')
                    entry['complete']=True
                    for row in entry['entries'].values():
                        if not any(o['memory_id']==row['memory_id'] for o in self.old.values()):
                            self.old[f'O{len(self.old)+1}']=dict(row,page_id=pid,scope=self.scope)
            self.pending[projection]=result['next_cursor']
            visible={r['memory_id'] for r in payload.get('entries',[])}
            result['old_refs']={k:v['memory_id'] for k,v in self.old.items() if v['memory_id'] in visible}
            bounded=fit(result,max_tokens=access.settings.wiki_read_tokens,max_bytes=min(access.settings.max_output_bytes,16384))
            if bounded is None:raise MemoryReadError('memory_budget_too_small')
            return bounded

    def require_page(self,pid):
        if pid not in self.pages or not self.pages[pid]['complete']:raise MemoryReadError('memory_read_incomplete')
        if self.writable is not None and pid not in self.writable:raise MemoryReadError('memory_page_not_writable')
        return self.pages[pid]['page']
