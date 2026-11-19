"""Wiki extraction binds symbolic evidence without retired kind or copied scope quotes."""
import pytest
from naiyue_test_agent.contracts.memory import MemoryReadError


def test_wiki_extraction_contract_has_no_kind():
    from naiyue_test_agent.application.memory.memory_wiki_protocol import WikiExtractionResult,bind_extraction
    schema=WikiExtractionResult.model_json_schema()
    assert 'kind' not in str(schema)
    with pytest.raises(MemoryReadError):bind_extraction({},dict(evidence_map={}),1)


def test_organization_contract_has_read_and_finish():
    from naiyue_test_agent.application.memory.memory_wiki_protocol import OrganizationResult
    assert OrganizationResult.model_validate(dict(result=dict(step='finish',decisions=[],page_changes=[],links=[])))
