"""Pydantic models for the agent contracts and run state.

These are also the source of truth for tool-use schemas: each agent that
returns structured output exposes a tool whose JSON schema is derived
directly from the model below.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# --- ICP -------------------------------------------------------------------


class ICP(BaseModel):
    """Structured ideal customer profile.

    Fields are individually optional, but at least one must be set — an
    empty ICP would match the entire SIRENE table.
    """

    naf_codes: list[str] = Field(
        default_factory=list,
        description="NAF activity codes, e.g. ['56.10A', '62.02A'].",
    )
    headcount_min: int | None = Field(
        default=None, ge=0, description="Lowest headcount accepted (in employees)."
    )
    headcount_max: int | None = Field(
        default=None, ge=0, description="Highest headcount accepted (in employees)."
    )
    region_code: str | None = Field(
        default=None, description="INSEE region code, e.g. '11' for Île-de-France."
    )
    department_codes: list[str] = Field(
        default_factory=list,
        description="INSEE department codes, e.g. ['75', '92'].",
    )
    postal_codes: list[str] = Field(
        default_factory=list, description="Specific postal codes to filter to."
    )
    age_max_months: int | None = Field(
        default=None, ge=0, description="Max months since legal creation."
    )
    age_min_months: int | None = Field(
        default=None, ge=0, description="Min months since legal creation."
    )
    legal_status_in: list[str] = Field(
        default_factory=list,
        description="SIRENE diffusion status filter; defaults to public ('O').",
    )
    require_active: bool = Field(
        default=True, description="Exclude legally-closed entities."
    )

    @model_validator(mode="after")
    def at_least_one_filter(self) -> ICP:
        any_set = (
            bool(self.naf_codes)
            or self.headcount_min is not None
            or self.headcount_max is not None
            or self.region_code is not None
            or bool(self.department_codes)
            or bool(self.postal_codes)
            or self.age_max_months is not None
            or self.age_min_months is not None
        )
        if not any_set:
            raise ValueError(
                "ICP must specify at least one of naf_codes, headcount range, "
                "region/department/postal codes, or age range."
            )
        if (
            self.headcount_min is not None
            and self.headcount_max is not None
            and self.headcount_min > self.headcount_max
        ):
            raise ValueError("headcount_min must be ≤ headcount_max")
        if (
            self.age_min_months is not None
            and self.age_max_months is not None
            and self.age_min_months > self.age_max_months
        ):
            raise ValueError("age_min_months must be ≤ age_max_months")
        return self


# --- Company / Score / Lead ------------------------------------------------


class Company(BaseModel):
    siren: str = Field(min_length=9, max_length=9)
    siret_main: str = Field(min_length=14, max_length=14)
    name: str
    naf_code: str
    headcount_tranche: str  # SIRENE tranche code "00" .. "53"
    headcount_label: str    # human label, e.g. "10 to 19"
    region_code: str
    department_code: str
    postal_code: str
    commune: str
    creation_date: date
    is_active: bool


class Score(BaseModel):
    siren: str = Field(min_length=9, max_length=9)
    value: int = Field(ge=1, le=5)
    reason: str = Field(max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)


class Lead(BaseModel):
    company: Company
    score: Score


# --- Run state -------------------------------------------------------------


class TraceEvent(BaseModel):
    """One observable step in a run, surfaced to the Streamlit UI."""

    at: datetime
    agent: Literal["icp_parser", "search", "scorer", "pipeline"]
    kind: Literal["start", "tool_call", "tool_result", "finish", "error", "log"]
    payload: dict = Field(default_factory=dict)
    cost_cents: float = 0.0
    duration_ms: int | None = None


class RunState(BaseModel):
    """LangGraph-shared state for one run.

    All fields are mutated by node functions in `graph.py`. The trace is
    append-only.
    """

    nl_query: str
    icp: ICP | None = None
    candidates: list[Company] = Field(default_factory=list)
    scores: list[Score] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    cost_cents: float = 0.0
    started_at: datetime
    error: str | None = None


# --- Tool-call payloads (for the agent tool-use definitions) ---------------


class SubmitICPArgs(BaseModel):
    """Args for the `submit_icp` tool exposed to the parser agent."""

    icp: ICP


class SubmitScoreArgs(BaseModel):
    """Args for the `submit_score` tool exposed to the scorer agent."""

    score: Score
