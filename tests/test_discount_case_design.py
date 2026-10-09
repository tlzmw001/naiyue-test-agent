from copy import deepcopy

import pytest
from langchain.agents.structured_output import StructuredOutputValidationError
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import Field

from naiyue_test_agent.test_agent.discount_case_design import (
    build_discount_case_agent,
    design_discount_priority_case,
)


PUBLIC_RULES = (
    'Rules are evaluated by priority. A higher-priority rule wins when multiple conditions match.\n'
    '| Priority | Rule | Condition | Decision |\n'
    '| 1 | Blocked user | `user_level = black` | `eligible=false`, `discount_rate=1.0`, `reason_code=USER_BLOCKED` |\n'
    '| 2 | Empty stock | `stock = 0` | `eligible=false`, `discount_rate=1.0`, `reason_code=STOCK_EMPTY` |\n'
    '| 3 | Campaign | `scene = campaign` and `stock > 0` | `eligible=true`, `discount_rate=0.8`, `reason_code=CAMPAIGN` |\n'
)

VALID_CASE = {
    'title': 'Blocked user wins when other rules also match',
    'method': 'POST',
    'path': '/api/v1/discount/policy',
    'request_body': {
        'user_id': 'u_priority',
        'user_level': 'black',
        'item_id': 'item_priority',
        'item_price': 120.5,
        'scene': 'campaign',
        'stock': 0,
        'request_id': 'req_priority',
    },
    'expected_status': 200,
    'expected_body': {
        'code': 0,
        'eligible': False,
        'discount_rate': 1.0,
        'reason_code': 'USER_BLOCKED',
        'request_id': 'req_priority',
    },
    'evidence_quotes': [
        'Rules are evaluated by priority. A higher-priority rule wins when multiple conditions match.',
        '| 1 | Blocked user | `user_level = black` | `eligible=false`, `discount_rate=1.0`, `reason_code=USER_BLOCKED` |',
    ],
}


class RecordingFake(FakeMessagesListChatModel):
    seen: list[list] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def make_agent(case):
    response = AIMessage(
        content='',
        tool_calls=[{'name': 'DiscountCase', 'args': case, 'id': 'case-call-1'}],
    )
    model = RecordingFake(responses=[response])
    selections = []
    agent = build_discount_case_agent(
        model,
        lambda session_id: ('fake-A', model),
        lambda session_id, model_id: selections.append((session_id, model_id)),
    )
    return agent, model, selections


def test_designs_case_from_public_rules_through_agent():
    agent, model, selections = make_agent(VALID_CASE)

    result = design_discount_priority_case(agent, PUBLIC_RULES, 's1')

    assert result.case.request_body.user_level == 'black'
    assert result.case.request_body.scene == 'campaign'
    assert result.case.request_body.stock == 0
    assert result.case.expected_body.reason_code == 'USER_BLOCKED'
    assert result.case.evidence_quotes == VALID_CASE['evidence_quotes']
    assert selections == [('s1', 'fake-A')]
    assert len(model.seen) == 1
    assert PUBLIC_RULES in model.seen[0][-1].content
    assert [message.type for message in result.messages] == ['human', 'ai', 'tool']
    assert result.messages[-1].tool_call_id == result.messages[-2].tool_calls[0]['id']


def test_empty_document_fails_before_model_call():
    agent, model, selections = make_agent(VALID_CASE)

    with pytest.raises(ValueError, match='public_doc'):
        design_discount_priority_case(agent, '  ', 's1')

    assert model.seen == []
    assert selections == []


def test_missing_required_field_is_not_silently_repaired():
    invalid_case = deepcopy(VALID_CASE)
    del invalid_case['request_body']['stock']
    agent, model, selections = make_agent(invalid_case)

    with pytest.raises(StructuredOutputValidationError, match='DiscountCase'):
        design_discount_priority_case(agent, PUBLIC_RULES, 's1')

    assert len(model.seen) == 1
    assert selections == [('s1', 'fake-A')]


def test_quote_outside_public_document_is_rejected():
    unsupported_case = deepcopy(VALID_CASE)
    unsupported_case['evidence_quotes'] = ['The source code proves this rule.']
    agent, _, _ = make_agent(unsupported_case)

    with pytest.raises(ValueError, match='evidence'):
        design_discount_priority_case(agent, PUBLIC_RULES, 's1')


def test_request_id_must_match_between_request_and_expectation():
    inconsistent_case = deepcopy(VALID_CASE)
    inconsistent_case['expected_body']['request_id'] = 'another_request'
    agent, _, _ = make_agent(inconsistent_case)

    with pytest.raises(ValueError, match='request_id'):
        design_discount_priority_case(agent, PUBLIC_RULES, 's1')


def test_model_error_propagates_after_selection():
    class FailingModel(RecordingFake):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError('model unavailable')

    model = FailingModel(responses=[AIMessage(content='unused')])
    selections = []
    agent = build_discount_case_agent(
        model,
        lambda session_id: ('fake-A', model),
        lambda session_id, model_id: selections.append((session_id, model_id)),
    )

    with pytest.raises(RuntimeError, match='model unavailable'):
        design_discount_priority_case(agent, PUBLIC_RULES, 's1')
    assert selections == [('s1', 'fake-A')]
