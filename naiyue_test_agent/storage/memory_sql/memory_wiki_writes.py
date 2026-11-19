"""Atomic, program-compiled Wiki groups; source authenticity is bound by the application."""
import json
import logging
import math
import sqlite3
from uuid import uuid4
from naiyue_test_agent.contracts.memory import MemoryReadError
from .writes import MemoryWriter, FIELDS as OLD_FIELDS, SOURCE_FIELDS
from .memory_wiki_schema import owner, TOPICS

FIELDS=tuple(k for k in OLD_FIELDS if k!='kind')+('page_id',)
SOURCES=SOURCE_FIELDS+('supports_scope',)
IDENTITY=('source_workspace_id','session_id','thread_id','message_id','revision','conversation_revision','start_offset','end_offset')
logger=logging.getLogger(__name__)


def _fail(code):
    raise MemoryReadError(code)


def _identity(source):
    return tuple(source[k] for k in IDENTITY)


class WikiWriter(MemoryWriter):
    def __init__(self,connection,scope,workspace_id,*,now,capacity):
        super().__init__(connection)
        self.rollback_failed=False
        self.generation_id=owner(connection,scope,workspace_id)['generation_id']
        if type(capacity) is not int or not 1<=capacity<=200:_fail('memory_capacity_invalid')
        if not math.isfinite(now):_fail('memory_clock_invalid')
        self.scope,self.workspace_id,self.now,self.capacity=scope,workspace_id,now,capacity

    def page(self,page_id):
        from .memory_wiki_queries import page
        return page(self._connection,self.scope,self.workspace_id,page_id)

    def snapshot(self):
        from .memory_wiki_queries import view
        return view(self._connection,self.scope,self.workspace_id,dict(action="list_topics"),now=self.now,include_candidates=True)

    @staticmethod
    def new_page(topic,title,description):
        return dict(page_id=uuid4().hex,expected_version=None,topic=topic,title=title,description=description)

    def apply(self,*,pages=(),memories=(),links=(),external_targets=()):
        c=self._connection
        if not c.in_transaction:raise RuntimeError('wiki_transaction_required')
        c.execute('SAVEPOINT wiki_group')
        try:
            result=self._apply(pages,memories,links,external_targets)
            c.execute('RELEASE wiki_group')
            return result
        except BaseException as error:
            try:
                c.execute('ROLLBACK TO wiki_group');c.execute('RELEASE wiki_group')
            except sqlite3.Error as secondary:
                self.rollback_failed=True
                logger.error('memory_wiki_group_rollback_failed error_type=%s',type(secondary).__name__)
            if isinstance(error,sqlite3.IntegrityError):
                logger.error('memory_wiki_group_write_failed error_type=%s', type(error).__name__)
                raise MemoryReadError('memory_wiki_invalid') from error
            raise

    def _apply(self,pages,memories,links,external_targets):
        c=self._connection
        if len({p['page_id'] for p in pages})!=len(pages):_fail('memory_duplicate_target')
        if len({m['row']['memory_id'] for m in memories})!=len(memories):_fail('memory_duplicate_target')
        expected_pages={p['page_id']:p for p in pages}
        counts={}
        # Validate all page snapshots before any member is changed.
        for p in pages:
            old=c.execute('SELECT * FROM naiyue_memory_pages WHERE page_id=?',(p['page_id'],)).fetchone()
            if (old is None)!=(p['expected_version'] is None) or old is not None and old['version']!=p['expected_version']:
                _fail('memory_conflict')
            if p['topic'] not in TOPICS:_fail('memory_topic_invalid')
            counts[p['page_id']]=self._count(p['page_id'])
        prepared=[]
        for item in memories:
            row=item['row'];mid=row['memory_id'];sources=item['sources']
            if (row['scope'],row['workspace_id'])!=(self.scope,self.workspace_id):_fail('memory_scope_mismatch')
            old=self.current(mid,self.scope,self.workspace_id)
            if (old is None)!=(item['expected_version'] is None) or old is not None and old['version']!=item['expected_version']:
                _fail('memory_conflict')
            if row['version']!=(old['version']+1 if old else 1):_fail('memory_conflict')
            if old and item.get('require_unexpired',True) and old['expires_at']<=self.now:
                _fail('memory_conflict')
            needed={row['page_id']} | ({old['page_id']} if old else set())
            if not needed<=expected_pages.keys():_fail('memory_page_version_required')
            if not sources or len(sources)>8 or len({_identity(s) for s in sources})!=len(sources):_fail('memory_source_capacity')
            if any(s.get('evidence_format')!=1 or s.get('verified_at') is None for s in sources):_fail('memory_source_unverified')
            supports=[s for s in sources if s.get('supports_scope')]
            if self.scope=='personal' and not supports:_fail('memory_scope_evidence_required')
            if self.scope=='workspace' and supports:_fail('memory_scope_mismatch')
            if any(s.get('role')!='human' or s.get('user_origin') is not True for s in supports):_fail('memory_scope_evidence_required')
            if old:
                required={_identity(s) for s in self.sources(mid,old['version']) if s['supports_scope']}
                if not required<={_identity(s) for s in supports}:_fail('memory_scope_evidence_required')
            elif c.execute("SELECT 1 FROM sqlite_master WHERE name='legacy143bm_memories'").fetchone() and c.execute(
                    'SELECT 1 FROM legacy143bm_memories WHERE memory_id=?',(mid,)).fetchone():_fail('memory_legacy_id')
            if any(not isinstance(row[k],(int,float)) or not math.isfinite(row[k]) for k in ('evidence_at','expires_at','created_at','updated_at')):
                _fail('memory_clock_invalid')
            prepared.append((row,sources))
        for p in pages:
            if p['expected_version'] is None:
                c.execute('INSERT INTO naiyue_memory_pages VALUES(?,?,?,?,?,?,1,?,?)',
                    (p['page_id'],self.scope,self.workspace_id,p['topic'],p['title'],p['description'],self.now,self.now))
            else:
                c.execute('''UPDATE naiyue_memory_pages SET topic=?,title=?,description=?,version=version+1,updated_at=?
                    WHERE page_id=?''',(p['topic'],p['title'],p['description'],self.now,p['page_id']))
        for row,sources in prepared:self.save(row,sources)
        for link_set in links:
            from_id=link_set['page_id']
            if from_id not in expected_pages:_fail('memory_page_version_required')
            targets=link_set['targets']
            if len(targets)>8 or len({(t['scope'],t['page_id']) for t in targets})!=len(targets):_fail('memory_link_capacity')
            c.execute('DELETE FROM naiyue_memory_page_links WHERE from_page_id=?',(from_id,))
            for target in targets:
                if target['scope']==self.scope:
                    exists=c.execute('SELECT 1 FROM naiyue_memory_pages WHERE page_id=?',(target['page_id'],)).fetchone()
                    if not exists:_fail('memory_link_target')
                elif self.scope=='workspace' and target['scope']=='personal':
                    if not any(t.get('scope')=='personal' and t.get('workspace_id') is None and t.get('page_id')==target['page_id'] for t in external_targets):
                        _fail('memory_link_target')
                else:_fail('memory_scope_mismatch')
                c.execute('INSERT INTO naiyue_memory_page_links VALUES(?,?,?,?)',
                    (from_id,target['scope'],target['page_id'],target['description']))
        for pid,before in counts.items():
            after=self._count(pid)
            if after>self.capacity and after>before:_fail('memory_topic_full')
        if pages:
            c.execute('UPDATE naiyue_memory_info SET catalog_revision=catalog_revision+1')
        return dict(page_ids=list(expected_pages),memory_ids=[r['memory_id'] for r,_ in prepared])

    def _count(self,page_id):
        return self._connection.execute('''SELECT COUNT(*) FROM naiyue_memories WHERE page_id=?
            AND status IN ('active','candidate') AND expires_at>?''',(page_id,self.now)).fetchone()[0]

    def save(self,row,sources):
        c=self._connection
        c.execute('UPDATE naiyue_memory_versions SET superseded_at=? WHERE memory_id=? AND superseded_at IS NULL',
            (self.now,row['memory_id']))
        c.execute('INSERT INTO naiyue_memories('+','.join(FIELDS)+') VALUES('+','.join('?' for _ in FIELDS)+
            ') ON CONFLICT(memory_id) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in FIELDS[1:]),tuple(row[k] for k in FIELDS))
        c.execute('INSERT INTO naiyue_memory_versions VALUES(?,?,?,NULL)',
            (row['memory_id'],row['version'],json.dumps(row,ensure_ascii=False,allow_nan=False)))
        c.executemany('INSERT INTO naiyue_memory_sources(memory_id,memory_version,'+','.join(SOURCES)+') VALUES('+
            ','.join('?' for _ in range(len(SOURCES)+2))+')',
            [(row['memory_id'],row['version'],*(s.get(k) for k in SOURCES)) for s in sources])
