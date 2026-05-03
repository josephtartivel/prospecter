"""Tests for the Scorer agent.

A `FakeLLM` stub returns pre-canned LiteLLM-shaped tool-call responses
keyed by the candidate SIREN. No real model calls. Verifies:

- input order preserved end-to-end
- one retry on Pydantic validation error
- candidate dropped + trace event appended after a second failure
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from prospecter.agents.scorer import score_candidates
from prospecter.schemas import ICP, Company, TraceEvent


def _ok(
    siren: str, value: int = 4, reason: str = "fits", confidence: float = 0.9
) -> dict[str, Any]:
    """Tool-call response shaped like a LiteLLM completion dict."""
    args = json.dumps({"siren": siren, "value": value, "reason": reason, "confidence": confidence})
    return {
        "choices": [
            {"message": {"tool_calls": [{"function": {"name": "submit_score", "arguments": args}}]}}
        ]
    }


def _bad(siren: str) -> dict[str, Any]:
    """Out-of-range `value` triggers a Pydantic validation error (Score expects 1..5)."""
    args = json.dumps({"siren": siren, "value": 99, "reason": "x", "confidence": 0.5})
    return {
        "choices": [
            {"message": {"tool_calls": [{"function": {"name": "submit_score", "arguments": args}}]}}
        ]
    }


class FakeLLM:
    """Stub LLM that pops a pre-canned response per (siren, attempt).

    Identifies the candidate by parsing the first user message — the
    payload built by `_build_user_payload` is a JSON object whose
    `company.siren` is the row identifier.
    """

    def __init__(self, responses_by_siren: dict[str, list[dict[str, Any]]]) -> None:
        self._queues = {k: list(v) for k, v in responses_by_siren.items()}
        self.call_count: dict[str, int] = dict.fromkeys(responses_by_siren, 0)

    def call(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Any = None,
        tool_choice: Any = None,
        max_tokens: int = 256,
        temperature: float = 0.0,
        cache_system_prompt: bool = False,
    ) -> dict[str, Any]:
        siren = self._extract_siren(messages)
        self.call_count[siren] = self.call_count.get(siren, 0) + 1
        queue = self._queues.get(siren, [])
        if not queue:
            raise AssertionError(f"FakeLLM had no canned response left for siren={siren}")
        return queue.pop(0)

    @staticmethod
    def _extract_siren(messages: list[dict[str, Any]]) -> str:
        for m in messages:
            if m.get("role") != "user":
                continue
            content = m.get("content")
            if not isinstance(content, str):
                continue
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue
            company = payload.get("company") if isinstance(payload, dict) else None
            if isinstance(company, dict) and "siren" in company:
                return company["siren"]
        raise AssertionError("could not locate candidate SIREN in messages")


@pytest.fixture
def icp() -> ICP:
    return ICP(naf_codes=["62.02A"], headcount_min=10, headcount_max=49)


@pytest.fixture
def candidates() -> list[Company]:
    base = {
        "name": "x",
        "naf_code": "62.02A",
        "headcount_tranche": "11",
        "headcount_label": "10 to 19",
        "region_code": "11",
        "department_code": "75",
        "postal_code": "75001",
        "commune": "Paris",
        "creation_date": date(2023, 1, 1),
        "is_active": True,
    }
    return [
        Company(siren="000000001", siret_main="00000000100001", **base),
        Company(siren="000000002", siret_main="00000000200001", **base),
        Company(siren="000000003", siret_main="00000000300001", **base),
    ]


async def test_ordering_matches_input(icp: ICP, candidates: list[Company]) -> None:
    fake = FakeLLM(
        {
            "000000001": [_ok("000000001", value=5)],
            "000000002": [_ok("000000002", value=4)],
            "000000003": [_ok("000000003", value=3)],
        }
    )
    scores = await score_candidates(icp, candidates, llm=fake, model="fake")  # type: ignore[arg-type]
    assert [s.siren for s in scores] == ["000000001", "000000002", "000000003"]
    assert [s.value for s in scores] == [5, 4, 3]


async def test_retry_on_validation_error(icp: ICP, candidates: list[Company]) -> None:
    fake = FakeLLM(
        {
            "000000001": [_bad("000000001"), _ok("000000001", value=2)],
            "000000002": [_ok("000000002")],
            "000000003": [_ok("000000003")],
        }
    )
    scores = await score_candidates(icp, candidates, llm=fake, model="fake")  # type: ignore[arg-type]
    assert len(scores) == 3
    assert fake.call_count["000000001"] == 2
    a = next(s for s in scores if s.siren == "000000001")
    assert a.value == 2  # the retry's value won, not the bad one


async def test_drop_on_second_failure(icp: ICP, candidates: list[Company]) -> None:
    fake = FakeLLM(
        {
            "000000001": [_ok("000000001")],
            "000000002": [_bad("000000002"), _bad("000000002")],
            "000000003": [_ok("000000003")],
        }
    )
    trace: list[TraceEvent] = []
    scores = await score_candidates(
        icp,
        candidates,
        llm=fake,  # type: ignore[arg-type]  # FakeLLM is duck-typed for testing
        model="fake",
        trace=trace,
    )
    assert [s.siren for s in scores] == ["000000001", "000000003"]
    assert fake.call_count["000000002"] == 2
    failures = [e for e in trace if e.payload.get("siren") == "000000002"]
    assert len(failures) == 1
    assert failures[0].agent == "scorer"
    assert failures[0].kind == "error"
    assert failures[0].payload.get("reason") == "scoring failed"


async def test_empty_candidates_short_circuits() -> None:
    fake = FakeLLM({})
    icp = ICP(naf_codes=["62.02A"])
    scores = await score_candidates(icp, [], llm=fake, model="fake")  # type: ignore[arg-type]
    assert scores == []
