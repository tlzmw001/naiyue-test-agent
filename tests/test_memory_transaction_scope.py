"""Application computation and mandatory receipts stay inside storage custody."""

import pytest
from langchain_core.messages import HumanMessage

from naiyue_test_agent.application.memory import memory_input_units, memory_wiki_mutations
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
from test_memory_wiki_mutations import args
from test_memory_batches import archive, batch, save
from test_memory_lifecycle import source
from test_memory_lifecycle_storage import memory_rows
from test_memory_readonly import memory


def test_batch_budget_computation_keeps_one_source_transaction(archive, monkeypatch):
    save(archive, [HumanMessage(id='source', content='visible source')])
    original, observed = memory_input_units.tokens, []
    def tokens(*args, **kwargs):
        observed.append((archive.database._active, archive.database.connection.in_transaction))
        return original(*args, **kwargs)
    monkeypatch.setattr(memory_input_units, 'tokens', tokens)
    result = batch(archive)
    assert result['units'][0]['text'] == 'visible source'
    assert observed and all(active and transaction for active, transaction in observed)
    assert not archive.database._active and not archive.database.connection.in_transaction


@pytest.mark.parametrize('scope', ['workspace', 'personal'])
@pytest.mark.parametrize('stage', ['receipt', 'after_apply'])
def test_failed_write_rolls_back_every_projection_and_releases_source(memory, monkeypatch, scope, stage):
    storage, access, identity, _ = memory
    storage.memory.prepare_wiki(now=1000.)
    ref = source(access, 'caller')
    _, repo = access._stores(scope)[0]
    target = repo.database
    def projections():
        return dict(memory_rows(target),
            pages=target.read_rows('SELECT * FROM naiyue_memory_pages ORDER BY page_id'),
            links=target.read_rows('SELECT * FROM naiyue_memory_page_links'),
            info=target.read_rows('SELECT * FROM naiyue_memory_info'))
    before = projections()
    observed = []
    failure = MemoryReadError('memory_output_budget' if stage == 'receipt' else 'memory_injected_write_failed')
    def observe():
        observed.append((storage.database._active, target._active,
            storage.database.connection.in_transaction, target.connection.in_transaction,
            target.connection.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]))
    if stage == 'receipt':
        original = memory_wiki_mutations.fit
        def fail(*a, **kw):
            observe()
            raise failure
        monkeypatch.setattr(memory_wiki_mutations, 'fit', fail)
    else:
        original = WikiWriter.apply
        def fail(writer, **kw):
            original(writer, **kw)
            observe()
            raise failure
        monkeypatch.setattr(WikiWriter, 'apply', fail)
    request = dict(args(sources=[ref]), scope=scope,
        **({'scope_source_indexes':[0]} if scope == 'personal' else {}))
    with pytest.raises(MemoryReadError) as raised:
        access.query_wiki('caller', identity, request, now=1000.)
    assert raised.value is failure
    assert observed == [(True, True, True, True, 0 if stage == 'receipt' else 1)]
    assert projections() == before
    assert not storage.database._active and not target._active
    assert not storage.database.connection.in_transaction and not target.connection.in_transaction
    if stage == 'receipt':monkeypatch.setattr(memory_wiki_mutations, 'fit', original)
    else:monkeypatch.setattr(WikiWriter, 'apply', original)
    saved = access.query_wiki('caller', identity, request, now=1000.)
    assert saved['status'] == 'saved' and saved['item']['version'] == 1
