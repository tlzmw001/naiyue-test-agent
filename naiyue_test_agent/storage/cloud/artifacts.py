"""Trusted artifact index and quota reservation; no task DB or filesystem access."""
from naiyue_test_agent.contracts.cloud_artifacts import ArtifactTransfer
from naiyue_test_agent.contracts.cloud_tasks import CloudError


class ArtifactRepository:
    def __init__(self,database):self.database=database

    @staticmethod
    def key(task_id,artifact_id):return 'artifact:'+task_id+':'+artifact_id

    def get(self,task_id,artifact_id):
        with self.database.transaction() as c:
            row=c.execute('SELECT value FROM meta WHERE key=?',(self.key(task_id,artifact_id),)).fetchone()
            if row is None:raise CloudError('artifact_not_found',404)
            return ArtifactTransfer.model_validate_json(row[0])

    def prepare(self,artifact,limits):
        key=self.key(artifact.task_id,artifact.artifact_id)
        with self.database.transaction(write=True) as c:
            row=c.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
            if row:
                value=ArtifactTransfer.model_validate_json(row[0])
                if value.artifact!=artifact:raise CloudError('artifact_identity_conflict')
                return value
            values=[ArtifactTransfer.model_validate_json(r[0]) for r in c.execute('SELECT value FROM meta WHERE key LIKE ?',('artifact:%',))]
            if len(values)>=limits.max_artifacts or sum(v.artifact.size for v in values)+artifact.size>limits.max_cache_bytes:
                raise CloudError('artifact_capacity')
            value=ArtifactTransfer(artifact=artifact,state='pending')
            c.execute('INSERT INTO meta(key,value) VALUES(?,?)',(key,value.model_dump_json()))
            return value

    def save(self,previous,replacement):
        with self.database.transaction(write=True) as c:
            if c.execute('UPDATE meta SET value=? WHERE key=? AND value=?',
                    (replacement.model_dump_json(),self.key(previous.artifact.task_id,previous.artifact.artifact_id),
                        previous.model_dump_json())).rowcount!=1:raise CloudError('artifact_state_conflict')
        return replacement

    def available(self,previous,identity):
        return self.save(previous,previous.model_copy(update={'state':'available','blob_identity':identity}))
