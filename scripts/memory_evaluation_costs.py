"""Per-case evaluation observations; no production policy or transport changes."""
import time


class CaseCosts:
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.began=clock();self.models={};self.reads=[];self.comparisons=[]

    def start(self,identity,stage):
        self.models[identity]=dict(stage=stage,start=self.clock(),duration_ms=None,usage=None,failed=False)

    def finish(self,identity,usage=None,*,failed=False):
        row=self.models.get(identity)
        if row is None or row['duration_ms'] is not None:return
        row.update(duration_ms=(self.clock()-row['start'])*1000,usage=usage,failed=failed)

    def observe(self,memory):
        query=memory.query;comparison=memory.comparison_candidates
        def observed(*args,**kwargs):
            request=kwargs.get('request') if 'request' in kwargs else args[2]
            row=dict(action=request['action'],failed=False);start=self.clock()
            try:return query(*args,**kwargs)
            except BaseException:
                row['failed']=True;raise
            finally:
                row['duration_ms']=(self.clock()-start)*1000;self.reads.append(row)
        def compared(*args,**kwargs):
            row=dict(planned_queries=len(kwargs['plan']['queries']),failed=False);start=self.clock()
            try:return comparison(*args,**kwargs)
            except BaseException:
                row['failed']=True;raise
            finally:
                row['duration_ms']=(self.clock()-start)*1000;self.comparisons.append(row)
        memory.query=observed;memory.comparison_candidates=compared
        def restore():memory.query=query;memory.comparison_candidates=comparison
        return restore

    def snapshot(self):
        stages={}
        for stage in ('extract','compare','foreground','followup'):
            rows=[r for r in self.models.values() if r['stage']==stage]
            known=[r['usage'] for r in rows if isinstance(r['usage'],dict) and
                all(type(r['usage'].get(k)) is int and r['usage'][k]>=0 for k in ('input_tokens','output_tokens'))]
            missing=len(rows)-len(known)
            stages[stage]=dict(logical_calls=len(rows),failed=sum(r['failed'] for r in rows),
                unfinished=sum(r['duration_ms'] is None for r in rows),unknown_usage_calls=missing,
                input_tokens=None if missing else sum(u['input_tokens'] for u in known),
                output_tokens=None if missing else sum(u['output_tokens'] for u in known),
                known_input_tokens=sum(u['input_tokens'] for u in known),known_output_tokens=sum(u['output_tokens'] for u in known),
                model_ms=None if any(r['duration_ms'] is None for r in rows) else sum(r['duration_ms'] for r in rows))
        return dict(total_ms=(self.clock()-self.began)*1000,models=stages,sdk_attempts=None,
            reads={action:dict(calls=sum(r['action']==action for r in self.reads),
                failed=sum(r['failed'] for r in self.reads if r['action']==action),
                duration_ms=sum(r['duration_ms'] for r in self.reads if r['action']==action)) for action in ('list','search','read')},
            comparison_reads=dict(groups=len(self.comparisons),planned_queries=sum(r['planned_queries'] for r in self.comparisons),
                failed=sum(r['failed'] for r in self.comparisons),duration_ms=sum(r['duration_ms'] for r in self.comparisons)),
            sql_operations=None,wait_ms=None,commit_ms=None,preload_tokens=None,
            boundary='model callbacks and MemoryAccess calls; excludes seed writes; not SQL/HTTP accounting')
