"""Design one discount-policy HTTP test from a supplied public contract."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage
from pydantic import BaseModel, ConfigDict, Field

from naiyue_test_agent.agent_runtime import build_runtime_agent


class DiscountRequestCase(BaseModel):
    model_config = ConfigDict(extra='forbid')

    user_id: str = Field(min_length=1)
    user_level: Literal['normal', 'vip', 'black']
    item_id: str = Field(min_length=1)
    item_price: float = Field(ge=0)
    scene: Literal['checkout', 'campaign', 'fallback']
    stock: int = Field(ge=0)
    request_id: str = Field(min_length=1)


class DiscountExpectedCase(BaseModel):
    model_config = ConfigDict(extra='forbid')

    code: int
    eligible: bool
    discount_rate: float
    reason_code: str = Field(min_length=1)
    request_id: str = Field(min_length=1)


class DiscountCase(BaseModel):
    """A proposed HTTP case, with verbatim evidence from the public contract."""

    model_config = ConfigDict(extra='forbid')

    title: str = Field(min_length=1)
    method: Literal['GET', 'POST', 'PUT', 'DELETE']
    path: str = Field(min_length=1)
    request_body: DiscountRequestCase
    expected_status: int = Field(ge=100, le=599)
    expected_body: DiscountExpectedCase
    evidence_quotes: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class DesignResult:
    case: DiscountCase
    messages: list[AnyMessage]


def build_discount_case_agent(
    default_model: BaseChatModel,
    resolve_model: Callable[[str], tuple[str, BaseChatModel]],
    record_selection: Callable[[str, str], None],
):
    """Build a reusable agent; its middleware resolves the model for each call."""
    return build_runtime_agent(
        default_model,
        tools=[],
        system_prompt=(
            'Design one HTTP test case for discount-rule priority. '
            'Make blocked user, empty stock, and campaign conditions overlap. '
            'Derive the expected decision only from the supplied public contract. '
            'Include exact quotations from that contract as evidence. '
            'Do not call the HTTP service or use implementation source or existing tests.'
        ),
        response_format=ToolStrategy(DiscountCase, handle_errors=False),
        resolve_model=resolve_model,
        record_selection=record_selection,
    )


def design_discount_priority_case(agent, public_doc: str, session_id: str) -> DesignResult:
    """Ask the agent for a case and reject quotations absent from the supplied doc."""
    if not public_doc.strip():
        raise ValueError('public_doc must not be empty')

    result = agent.invoke(
        {'messages': [('user', f'Public API contract:\n{public_doc}\n\nDesign the priority-conflict case.')]},
        context={'session_id': session_id},
    )
    case = result['structured_response']
    if not isinstance(case, DiscountCase):
        raise ValueError('Agent did not return a DiscountCase')
    if case.request_body.request_id != case.expected_body.request_id:
        raise ValueError('request_id differs between request and expected response')
    for quote in case.evidence_quotes:
        if not quote.strip() or quote not in public_doc:
            raise ValueError('evidence quote is not in public_doc')
    return DesignResult(case=case, messages=result['messages'])
