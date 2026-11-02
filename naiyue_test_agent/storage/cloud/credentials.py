"""Credential SQL lives in storage; issuance/authorization policy lives above."""
import json

from naiyue_test_agent.contracts.cloud_tasks import StoredTaskCredential


class CredentialRepository:
    def __init__(self, database):
        self.database = database

    def save(self, record):
        record = StoredTaskCredential.model_validate(record)
        with self.database.transaction(write=True) as connection:
            connection.execute('''INSERT INTO task_credentials(credential_id,token_hash,task_id,project_id,
                generation,scopes_json,expires_at,revoked_at) VALUES(?,?,?,?,?,?,?,?)''',
                (record.credential_id, record.token_hash, record.task_id, record.project_id,
                 record.generation, json.dumps(record.scopes), record.expires_at, record.revoked_at))

    def find(self, token_hash):
        with self.database.transaction() as connection:
            row = connection.execute('SELECT * FROM task_credentials WHERE token_hash=?', (token_hash,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        value['scopes'] = json.loads(value.pop('scopes_json'))
        return StoredTaskCredential(**value)

    def revoke(self, task_id, credential_id, now):
        with self.database.transaction(write=True) as connection:
            connection.execute('UPDATE task_credentials SET revoked_at=? WHERE credential_id=? AND task_id=?',
                (now, credential_id, task_id))
