"""Immutable request measurements shared by runtime state and calibration."""
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class BudgetEstimate:
    status: str
    raw_input_tokens: int | None
    calibrated_input_tokens: int | None
    method: str
    invalidation_reason: str | None


@dataclass(frozen=True)
class RequestMeasurement:
    purpose: str
    media: str
    identity: str | None
    source_envelope: dict | None
    revision_high_water: int
    budget: BudgetEstimate
    sampling_eligible: bool
    calibration_ratio: Fraction | None
