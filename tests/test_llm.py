"""Sanity tests for the LiteLLM-backed wrapper.

The Langfuse callback must be opt-in: importing `prospecter.llm` with
`LANGFUSE_ENABLED=false` (or unset) must succeed without any Langfuse
key present and must not register the callback. Hard requirement of
ADR-007 — observability is free to add and free to skip.

Cost extraction tests stub `litellm_completion` so the LLM.call →
CallRecord → total_cost_usd path is covered without a network call —
the existing TestCostWiring stops at pipeline aggregation but never
exercises this seam, so a regression in `_hidden_params` parsing
would slip through.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import litellm
import pytest

import prospecter.llm as llm_mod


def test_import_disabled_requires_no_langfuse_keys(monkeypatch):
    """With LANGFUSE_ENABLED=false and no keys, importing llm must not crash
    and must not register the callback on the litellm singleton."""
    litellm.success_callback = []
    litellm.failure_callback = []
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.delenv(k, raising=False)

    importlib.reload(llm_mod)  # re-runs the module-level guard under the patched env

    assert "langfuse" not in (litellm.success_callback or [])
    assert "langfuse" not in (litellm.failure_callback or [])


# --- LLM.call cost extraction --------------------------------------------


def _fake_response(*, prompt_tokens: int, completion_tokens: int, response_cost):
    """Mimic the surface of a litellm `ModelResponse` that LLM.call reads.

    Only the attributes the wrapper actually touches are populated;
    everything else stays absent so an accidental new dependency would
    raise AttributeError instead of returning a silent default.
    """
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )
    # `_hidden_params` attribute name matches LiteLLM's actual surface
    # — it is what carries the per-call resolved cost on Anthropic,
    # OpenAI, Mistral, and DeepSeek alike.
    response._hidden_params = {"response_cost": response_cost}
    return response


def test_call_records_cost_from_hidden_params(monkeypatch):
    """Happy path: a normal ModelResponse with cost lands intact in history."""
    monkeypatch.setattr(
        llm_mod,
        "litellm_completion",
        lambda **kw: _fake_response(prompt_tokens=1500, completion_tokens=80, response_cost=0.0042),
    )

    llm = llm_mod.LLM(primary_model="stub-model")
    llm.call(messages=[{"role": "user", "content": "hi"}])

    assert llm.total_calls == 1
    rec = llm.history[0]
    assert rec.model == "stub-model"
    assert rec.in_tokens == 1500
    assert rec.out_tokens == 80
    assert rec.cost_usd == pytest.approx(0.0042)
    assert llm.total_cost_usd == pytest.approx(0.0042)


def test_call_handles_missing_response_cost(monkeypatch):
    """Defensive: a provider that omits `response_cost` must not crash;
    cost is recorded as 0 so accounting fails loud (zero-displayed) rather
    than silently swallowing a real number."""
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2))
    response._hidden_params = {}  # no response_cost key

    monkeypatch.setattr(llm_mod, "litellm_completion", lambda **kw: response)

    llm = llm_mod.LLM(primary_model="stub-model")
    llm.call(messages=[{"role": "user", "content": "hi"}])

    assert llm.history[0].cost_usd == 0.0
    assert llm.history[0].in_tokens == 10
    assert llm.history[0].out_tokens == 2


def test_call_accumulates_across_calls(monkeypatch):
    """Multiple calls add up — the property total_cost_usd is the sum."""
    costs = iter([0.001, 0.0032, 0.0007])

    def fake(**kw):
        return _fake_response(prompt_tokens=100, completion_tokens=10, response_cost=next(costs))

    monkeypatch.setattr(llm_mod, "litellm_completion", fake)

    llm = llm_mod.LLM(primary_model="stub-model")
    for _ in range(3):
        llm.call(messages=[{"role": "user", "content": "hi"}])

    assert llm.total_calls == 3
    assert llm.total_cost_usd == pytest.approx(0.001 + 0.0032 + 0.0007)
