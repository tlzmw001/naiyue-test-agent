"""Application computation and mandatory receipts stay inside storage custody."""

import pytest
from langchain_core.messages import HumanMessage

from naiyue_test_agent.application.memory import memory_batches, memory_results
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.tools.memory import MemoryInput
from test_memory_batches import archive, batch, save
from test_memory_lifecycle import source, proposal
from test_memory_lifecycle_storage import memory_rows
from test_memory_readonly import memory


def test_batch_budget_computation_keeps_one_source_transaction(archive, monkeypatch):
    save(archive, [HumanMessage(id='source', content='visible source')])
    original, observed = memory_batches._tokens, []
    def tokens(*args, **kwargs):
        observed.append((archive.database._active, archive.database.connection.in_transaction))
        return original(*args, **kwargs)
    monkeypatch.setattr(memory_batches, '_tokens', tokens)
    result = batch(archive)
    assert result['sources'][0]['text'] == 'visible source'
    assert observed and all(active and transaction for active, transaction in observed)
    assert not archive.database._active and not archive.database.connection.in_transaction


@pytest.mark.parametrize('scope', ['workspace', 'personal'])
def test_receipt_failure_rolls_back_every_projection_and_releases_source(memory, monkeypatch, scope):
    storage, access, identity, _ = memory
    ref = source(access, 'caller')
    _, repo = access._stores(scope)[0]
    target = repo.database
    before = memory_rows(target)
    original, observed = memory_results.fit_mutation, []
    failure = MemoryReadError('memory_output_budget')
    def fail(result, budget):
        observed.append((storage.database._active, target._active,
            storage.database.connection.in_transaction, target.connection.in_transaction,
            target.connection.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]))
        raise failure
    monkeypatch.setattr(memory_results, 'fit_mutation', fail)
    request = MemoryInput(action='record', scope=scope, sources=[ref], proposal=proposal()).model_dump(mode='json')
    with pytest.raises(MemoryReadError) as raised:
        access.query('caller', identity, request, now=1000.)
    assert raised.value is failure
    assert observed == [(True, True, True, True, 1)]
    assert memory_rows(target) == before
    assert not storage.database._active and not target._active
    assert not storage.database.connection.in_transaction and not target.connection.in_transaction
    monkeypatch.setattr(memory_results, 'fit_mutation', original)
    saved = access.query('caller', identity, request, now=1000.)
    assert saved['status'] == 'saved' and saved['item']['version'] == 1
