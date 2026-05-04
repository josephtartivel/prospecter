"""Tests for graph wiring + a gated end-to-end smoke test.

The unit tests monkeypatch the agent imports on ``prospecter.graph`` so
the *real* compiled LangGraph runs against stubs — no LLM, no DuckDB.
The smoke test at the bottom is skipped unless ``data/sirene/`` is
populated AND an LLM key is in the env; it exercises the full stack.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from prospecter import graph as graph_mod
from prospecter.schemas import ICP, Company, RunState, Score


def _stub_company(siren: str = "100000001") -> Company:
    return Company(
        siren=siren,
        siret_main=siren + "00001",
        name="Acme",
        naf_code="62.01Z",
        headcount_tranche="11",
        headcount_label="10 to 19",
        region_code="11",
        department_code="75",
        postal_code="75011",
        commune="Paris",
        creation_date=date(2020, 1, 1),
        is_active=True,
    )


def _initial_state() -> RunState:
    return RunState(nl_query="paris saas 10-49", started_at=datetime.now(tz=UTC))


def _stub_icp() -> ICP:
    return ICP(naf_codes=["62.01Z"], headcount_min=10, headcount_max=49)


@pytest.fixture
def patched_parse(monkeypatch):
    """Stub parse_icp to a fixed ICP so search/score nodes always see input."""
    monkeypatch.setattr(graph_mod, "parse_icp", lambda nl, *, llm: _stub_icp())


def test_search_node_happy_path(monkeypatch, patched_parse):
    cs = [_stub_company("100000001"), _stub_company("100000002")]
    monkeypatch.setattr(graph_mod, "search", lambda icp, *, store: cs)

    async def stub_score(icp, candidates, *, llm, trace=None):
        return [Score(siren=c.siren, value=3, reason="ok", confidence=0.5) for c in candidates]

    monkeypatch.setattr(graph_mod, "score_candidates", stub_score)

    app = graph_mod.build_graph(llm=object(), store=object())  # type: ignore[arg-type]
    state = RunState.model_validate(app.invoke(_initial_state()))

    assert state.error is None
    assert [c.siren for c in state.candidates] == ["100000001", "100000002"]
    finishes = [e for e in state.trace if e.agent == "search" and e.kind == "finish"]
    assert len(finishes) == 1
    assert finishes[0].payload["count"] == 2


def test_search_node_error_routes_to_end(monkeypatch, patched_parse):
    def boom(icp, *, store):
        raise RuntimeError("duckdb is on fire")

    monkeypatch.setattr(graph_mod, "search", boom)

    async def must_not_run(*a, **kw):  # pragma: no cover - asserted not called
        raise AssertionError("score_candidates ran despite search error")

    monkeypatch.setattr(graph_mod, "score_candidates", must_not_run)

    app = graph_mod.build_graph(llm=object(), store=object())  # type: ignore[arg-type]
    state = RunState.model_validate(app.invoke(_initial_state()))

    assert state.error is not None
    assert "duckdb is on fire" in state.error
    errors = [e for e in state.trace if e.agent == "search" and e.kind == "error"]
    assert len(errors) == 1
    # Conditional edge took us to END — scorer never started.
    assert [e for e in state.trace if e.agent == "scorer"] == []
    assert state.scores == []


def test_score_node_happy_path(monkeypatch, patched_parse):
    cs = [_stub_company("100000001"), _stub_company("100000002"), _stub_company("100000003")]
    monkeypatch.setattr(graph_mod, "search", lambda icp, *, store: cs)

    async def stub_score(icp, candidates, *, llm, trace=None):
        # Return out of value order — node must sort desc.
        return [
            Score(siren=candidates[0].siren, value=2, reason="meh", confidence=0.5),
            Score(siren=candidates[1].siren, value=5, reason="great", confidence=0.9),
            Score(siren=candidates[2].siren, value=3, reason="ok", confidence=0.6),
        ]

    monkeypatch.setattr(graph_mod, "score_candidates", stub_score)

    app = graph_mod.build_graph(llm=object(), store=object())  # type: ignore[arg-type]
    state = RunState.model_validate(app.invoke(_initial_state()))

    assert state.error is None
    assert [s.value for s in state.scores] == [5, 3, 2]
    finishes = [e for e in state.trace if e.agent == "scorer" and e.kind == "finish"]
    assert len(finishes) == 1
    assert finishes[0].payload == {"scored": 3, "candidates": 3}


def test_score_node_error_routes_to_end(monkeypatch, patched_parse):
    monkeypatch.setattr(graph_mod, "search", lambda icp, *, store: [_stub_company()])

    async def boom(icp, candidates, *, llm, trace=None):
        raise RuntimeError("provider 500")

    monkeypatch.setattr(graph_mod, "score_candidates", boom)

    app = graph_mod.build_graph(llm=object(), store=object())  # type: ignore[arg-type]
    state = RunState.model_validate(app.invoke(_initial_state()))

    assert state.error is not None
    assert "provider 500" in state.error
    errors = [e for e in state.trace if e.agent == "scorer" and e.kind == "error"]
    assert len(errors) == 1
    assert state.scores == []


class TestCostWiring:
    def test_run_aggregates_llm_cost_into_state(self, monkeypatch, tmp_path):
        """``pipeline.run`` must surface ``llm.total_cost_usd`` into
        ``state.cost_cents`` so the CLI banner and eval harness can read
        the real cost. Without this wire the displayed cost is always 0."""
        from prospecter import pipeline as pipeline_mod
        from prospecter.llm import LLM, CallRecord

        # Stub the agents inside the graph so the test stays unit-scoped:
        # parse → fixed ICP, search → one company, score → one Score.
        monkeypatch.setattr(graph_mod, "parse_icp", lambda nl, *, llm: _stub_icp())
        monkeypatch.setattr(graph_mod, "search", lambda icp, *, store: [_stub_company()])

        async def stub_score(icp, candidates, *, llm, trace=None):
            return [Score(siren=c.siren, value=3, reason="ok", confidence=0.5) for c in candidates]

        monkeypatch.setattr(graph_mod, "score_candidates", stub_score)

        # LLM stub with two pre-recorded calls totalling $0.0042 = 0.42 cents.
        stub_llm = LLM(primary_model="stub")
        stub_llm.history.append(
            CallRecord(model="stub", in_tokens=100, out_tokens=20, cost_usd=0.001, duration_ms=200)
        )
        stub_llm.history.append(
            CallRecord(model="stub", in_tokens=200, out_tokens=30, cost_usd=0.0032, duration_ms=400)
        )
        monkeypatch.setattr(LLM, "from_env", classmethod(lambda cls, **kw: stub_llm))
        # SireneStore is constructed but never queried (search is stubbed).
        monkeypatch.setattr(pipeline_mod, "SireneStore", lambda: object())

        leads, state = pipeline_mod.run("paris saas 10-49", output_dir=tmp_path)

        assert leads, "expected at least one lead from the stubbed pipeline"
        # 0.001 + 0.0032 = 0.0042 USD → 0.42 cents
        assert state.cost_cents == pytest.approx(0.42, abs=1e-6)


# --- gated end-to-end smoke test --------------------------------------------

DATA_DIR = Path("data/sirene")
_HAS_PARQUET = (DATA_DIR / "sirene.parquet").exists()
_HAS_CSVS = (DATA_DIR / "StockUniteLegale_utf8.csv").exists() and (
    DATA_DIR / "StockEtablissement_utf8.csv"
).exists()
DATA_AVAILABLE = _HAS_PARQUET or _HAS_CSVS

data_available = pytest.mark.skipif(
    not DATA_AVAILABLE,
    reason="SIRENE data not populated; run scripts/fetch_sirene.sh",
)
keys_available = pytest.mark.skipif(
    not any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")),
    reason="no LLM API key in env",
)


@data_available
@keys_available
def test_pipeline_smoke(tmp_path):
    """End-to-end run on real SIRENE + real LLM. Manual gate; CI skips."""
    from prospecter.pipeline import run as run_pipeline

    leads, state = run_pipeline("Paris SaaS startups, 10-49 employees", output_dir=tmp_path)
    assert state.error is None
    assert leads, "expected at least one ranked lead"
    csvs = list(Path(tmp_path).glob("*.csv"))
    assert len(csvs) == 1
    text = csvs[0].read_text(encoding="utf-8")
    assert text.startswith("score,confidence,siren,")
