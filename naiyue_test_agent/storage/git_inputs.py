"""Immutable input identity and required preparation results in the project registry."""
from dataclasses import asdict
import json

from naiyue_test_agent.contracts.git_inputs import GitInputRecord, GitInputError, input_id, input_path


class GitInputs:
    def __init__(self, database):
        self.database = database

    def reserve(self, project_id, revision, ident, include, settings):
        input_id(ident)
        if not isinstance(include,(list,tuple)) or any(not isinstance(p,str) for p in include):
            raise GitInputError('git_input_invalid_selection')
        selected = tuple(sorted(input_path(p) for p in include))
        if len(selected)>settings.max_files or len(set(selected))!=len(selected):
            raise GitInputError('git_input_invalid_selection')
        with self.database.transaction(write=True) as conn:
            stored = conn.execute('SELECT record_json FROM git_inputs WHERE input_id=?',(ident,)).fetchone()
            if stored is not None:
                row = GitInputRecord.from_dict(json.loads(stored['record_json']))
                if row.project_id!=project_id or row.include_untracked!=selected:
                    raise GitInputError('git_input_conflict')
                return row, False
            count = conn.execute('SELECT count(*) FROM git_inputs WHERE project_id=?',(project_id,)).fetchone()[0]
            if count >= settings.max_snapshot_records:
                raise GitInputError('git_input_capacity')
            row = GitInputRecord(ident,project_id,revision,selected,settings.model_dump(mode='json'))
            conn.execute('INSERT INTO git_inputs VALUES(?,?,?)',
                (ident,project_id,json.dumps(asdict(row),sort_keys=True)))
        return row, True

    def get(self, project_id, ident):
        input_id(ident)
        rows = self.database.read_rows('SELECT record_json FROM git_inputs WHERE input_id=? AND project_id=?',
            (ident,project_id))
        if not rows:
            raise GitInputError('git_input_not_found')
        return GitInputRecord.from_dict(json.loads(rows[0]['record_json']))

    def save(self, row):
        with self.database.transaction(write=True) as conn:
            changed = conn.execute('UPDATE git_inputs SET record_json=? WHERE input_id=? AND project_id=?',
                (json.dumps(asdict(row),sort_keys=True),row.input_id,row.project_id))
            if changed.rowcount!=1:
                raise GitInputError('git_input_not_found')
        return self.get(row.project_id,row.input_id)

    @staticmethod
    def _referenced(conn, project_id, ident):
        if conn.execute("""SELECT 1 FROM execution_environments WHERE project_id=?
                AND lifecycle!='destroyed' AND json_extract(record_json,'$.input_id')=? LIMIT 1""",
                (project_id,ident)).fetchone():return True
        if conn.execute("""SELECT 1 FROM sandbox_operations WHERE project_id=?
                AND json_extract(record_json,'$.kind')='git_result'
                AND (json_extract(record_json,'$.metadata.delivery.receipt.input_id')=?
                    OR binding_id IN (SELECT binding_id FROM execution_environments
                        WHERE project_id=? AND json_extract(record_json,'$.input_id')=?)) LIMIT 1""",
                (project_id,ident,project_id,ident)).fetchone():return True
        return conn.execute("""SELECT 1 FROM git_deliveries WHERE project_id=? AND binding_id IN (
            SELECT binding_id FROM execution_environments
            WHERE project_id=? AND json_extract(record_json,'$.input_id')=?) LIMIT 1""",
            (project_id,project_id,ident)).fetchone() is not None

    def can_retire(self, project_id, ident):
        with self.database.transaction() as conn:
            return not self._referenced(conn,project_id,ident)

    def delete_unreferenced(self, project_id, ident):
        input_id(ident)
        with self.database.transaction(write=True) as conn:
            stored = conn.execute('SELECT record_json FROM git_inputs WHERE input_id=? AND project_id=?',
                (ident,project_id)).fetchone()
            if stored is None:
                return False
            row = GitInputRecord.from_dict(json.loads(stored['record_json']))
            if row.state not in ('preparing','ready','failed') or self._referenced(conn,project_id,ident):
                return False
            conn.execute('DELETE FROM git_inputs WHERE input_id=? AND project_id=?',(ident,project_id))
            return True
