"""Pydantic v2 models for the venue rulebook config (spec.md §4).

Every venue-specific number and rule lives in a YAML file validated against
these models (R194) — nothing venue-specific is hardcoded in application
code. `extra="forbid"` everywhere so a typo'd or misplaced key fails loudly
at load time (R41) instead of being silently ignored.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rfq_mm.core.types import Segment

# --- venue / session / reference market -------------------------------------


class VenueMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    model: str
    operator: str
    source: str


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str
    open: str
    close: str
    post_trading_minutes: int = Field(ge=0)
    days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]]

    @model_validator(mode="after")
    def _valid_times(self) -> SessionConfig:
        time.fromisoformat(self.open)
        time.fromisoformat(self.close)
        return self


class ReferenceMarketHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open: str
    close: str

    @model_validator(mode="after")
    def _valid_times(self) -> ReferenceMarketHours:
        time.fromisoformat(self.open)
        time.fromisoformat(self.close)
        return self


class ReferenceMarketConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: str
    hours: ReferenceMarketHours


# --- quote windows (§8) ------------------------------------------------------


class QuoteWindowsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acceptance_window_seconds: int = Field(gt=0)  # §8(1)
    withdrawal_window_seconds: int = Field(gt=0)  # §8(2)
    firm_size_breach_halt_seconds: int = Field(gt=0)  # §8(3)


# --- obligations / exemptions (§5) -------------------------------------------


class AfterVenueOpenExemption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["after_venue_open"]
    minutes: int = Field(ge=0)


class AroundReferenceOpenExemption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["around_reference_open"]
    before_minutes: int = Field(ge=0)
    after_minutes: int = Field(ge=0)


class ReferenceHaltExemption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["reference_halt"]
    grace_minutes: int = Field(ge=0)


class ReferenceIndicativeOnlyExemption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["reference_indicative_only"]


class CorporateActionCumDayExemption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["corporate_action_cum_day"]
    after: str

    @model_validator(mode="after")
    def _valid_time(self) -> CorporateActionCumDayExemption:
        time.fromisoformat(self.after)
        return self


class MmSystemOutageExemption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["mm_system_outage"]


class SuspectedManipulationExemption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["suspected_manipulation"]


class ConformityCheckExemption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["conformity_check"]
    max_seconds: int = Field(gt=0)


class ExtremeMoveExemption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["extreme_move"]
    threshold_bps: float = Field(gt=0)
    window_seconds: int = Field(gt=0)


_AnyExemption = (
    AfterVenueOpenExemption
    | AroundReferenceOpenExemption
    | ReferenceHaltExemption
    | ReferenceIndicativeOnlyExemption
    | CorporateActionCumDayExemption
    | MmSystemOutageExemption
    | SuspectedManipulationExemption
    | ConformityCheckExemption
    | ExtremeMoveExemption
)
ExemptionConfig = Annotated[_AnyExemption, Field(discriminator="type")]


class ObligationsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_response_ratio: float = Field(gt=0, le=1)  # §5(1)
    exemptions: list[ExemptionConfig]


# --- spread rule (§7(1)) -----------------------------------------------------


class SpreadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: Literal["not_outside_reference_spread"]
    include_costs: list[Literal["trading", "settlement", "fx"]]
    depth_aware: bool


# --- guaranteed volumes (§7(1)) ----------------------------------------------


class GuaranteedVolumeBand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_open: float = Field(gt=0)
    ref_closed: float = Field(gt=0)


class GuaranteedVolumesConfig(BaseModel):
    """Mirrors the literal YAML shape: one {ref_open, ref_closed} block per
    Segment, plus two scalar keys (leveraged_scaling, discretionary_below_price_eur)
    at the same level. Pulled apart in a before-validator so the config file
    itself needs no extra nesting (R116m)."""

    model_config = ConfigDict(extra="forbid")

    bands: dict[Segment, GuaranteedVolumeBand]
    leveraged_scaling: float = Field(gt=0, le=1)
    discretionary_below_price_eur: float = Field(gt=0)

    @model_validator(mode="before")
    @classmethod
    def _split_scalars_from_bands(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw = dict(data)
        leveraged_scaling = raw.pop("leveraged_scaling", None)
        discretionary = raw.pop("discretionary_below_price_eur", None)
        missing = [s.value for s in Segment if s.value not in raw]
        if missing:
            raise ValueError(f"guaranteed_volumes_eur missing segments: {missing}")
        extra = [k for k in raw if k not in {s.value for s in Segment}]
        if extra:
            raise ValueError(f"guaranteed_volumes_eur has unknown keys: {extra}")
        return {
            "bands": raw,
            "leveraged_scaling": leveraged_scaling,
            "discretionary_below_price_eur": discretionary,
        }

    def volume_for(self, segment: Segment, *, reference_market_open: bool) -> float:
        band = self.bands[segment]
        return band.ref_open if reference_market_open else band.ref_closed


# --- surveillance (§12) / throttling (§13) -----------------------------------


class SurveillanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_rfq_per_participant_per_minute: int = Field(gt=0)
    max_cancel_ratio: float = Field(gt=0, le=1)


ThrottlingLevel = Literal[
    "none", "slow_low_priority", "reject_low_priority", "reject_all", "manual_reconnect"
]


class ThrottlingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    levels: list[ThrottlingLevel]


# --- root ---------------------------------------------------------------------


class VenueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    venue: VenueMeta
    session: SessionConfig
    reference_market: ReferenceMarketConfig
    quote: QuoteWindowsConfig
    obligations: ObligationsConfig
    spread: SpreadConfig
    guaranteed_volumes_eur: GuaranteedVolumesConfig
    surveillance: SurveillanceConfig
    throttling: ThrottlingConfig


def load_venue_config(path: str | Path) -> VenueConfig:
    """Load and validate a venue rulebook. Raises pydantic.ValidationError (with
    a precise field-level message) or yaml.YAMLError on a malformed file."""
    text = Path(path).read_text()
    raw = yaml.safe_load(text)
    return VenueConfig.model_validate(raw)
