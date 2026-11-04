"""Runner-owned capture identities, separate from Agent run completion/outbox."""
from naiyue_test_agent.contracts.cloud_artifacts import ArtifactCaptureState
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_events import digest
import json


class RunnerArtifactJournal:
    def __init__(self,database):
        self.database=database
        with database.transaction(write=True) as c:
            c.execute('CREATE TABLE IF NOT EXISTS nyt_runner_artifacts(command_id TEXT PRIMARY KEY,artifact_id TEXT UNIQUE NOT NULL,record_json TEXT NOT NULL)')
            for row in c.execute('SELECT command_id,record_json FROM nyt_runner_artifacts').fetchall():
                value=ArtifactCaptureState.model_validate_json(row['record_json'])
                if value.status=='capturing':
                    c.execute('UPDATE nyt_runner_artifacts SET record_json=? WHERE command_id=?',
                        (value.model_copy(update={'status':'unknown','error_code':'artifact_capture_unconfirmed'}).model_dump_json(),row['command_id']))

    def get(self,ident):
        with self.database.transaction() as c:
            row=c.execute('SELECT record_json FROM nyt_runner_artifacts WHERE command_id=?',(ident,)).fetchone()
            return ArtifactCaptureState.model_validate_json(row[0]) if row else None

    def list(self):
        with self.database.transaction() as c:
            return [ArtifactCaptureState.model_validate_json(r[0]) for r in c.execute('SELECT record_json FROM nyt_runner_artifacts')]

    def admit(self,command,*,rejection=None):
        with self.database.transaction(write=True) as c:
            rejected=c.execute('SELECT value FROM nyt_runner_meta WHERE key=?',('rejected:'+command.command_id,)).fetchone()
            if rejected:
                value=json.loads(rejected[0])
                if value['operation']!='artifact:capture' or value['request_digest']!=digest(command.model_dump()):raise CloudError('command_id_conflict')
                raise CloudError(value['error_code'])
            old=c.execute('SELECT record_json FROM nyt_runner_artifacts WHERE command_id=?',(command.command_id,)).fetchone()
            if old:
                value=ArtifactCaptureState.model_validate_json(old[0])
                if value.command!=command:raise CloudError('command_id_conflict')
                return value
            for row in c.execute('SELECT record_json FROM nyt_runner_artifacts'):
                if ArtifactCaptureState.model_validate_json(row[0]).status in ('capturing','captured','unknown'):
                    raise CloudError('artifact_busy')
            if c.execute('SELECT 1 FROM nyt_runner_artifacts WHERE artifact_id=?',(command.artifact_id,)).fetchone():
                raise CloudError('artifact_identity_conflict')
            value=ArtifactCaptureState(command=command,status='failed' if rejection else 'capturing',error_code=rejection)
            c.execute('INSERT INTO nyt_runner_artifacts VALUES(?,?,?)',(command.command_id,command.artifact_id,value.model_dump_json()))
            return value

    def save(self,value):
        with self.database.transaction(write=True) as c:
            if c.execute('UPDATE nyt_runner_artifacts SET record_json=? WHERE command_id=? AND artifact_id=?',
                    (value.model_dump_json(),value.command.command_id,value.command.artifact_id)).rowcount!=1:
                raise CloudError('artifact_identity_conflict')
        return value
