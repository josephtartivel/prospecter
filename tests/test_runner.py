"""Tests for ``eval.runner``.

We never call a real model here — ``pipeline.run`` is monkeypatched on
``eval.runner`` so the runner's I/O, scoping, and reporting paths are
exercised against stubs.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from eval import runner as runner_mod
from prospecter.schemas import Company, Lead, RunState, Score


def _company(siren: str) -> Company:
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


def _lead(siren: str, value: int = 5) -> Lead:
    return Lead(
        company=_company(siren),
        score=Score(siren=siren, value=value, reason="fits", confidence=0.9),
    )


def _state(cost_cents: float = 1.5) -> RunState:
    s = RunState(nl_query="x", started_at=datetime.now(tz=UTC))
    s.cost_cents = cost_cents
    return s


# --- load_icps -----------------------------------------------------------


class TestLoadIcps:
    def test_parses_jsonl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "icps.jsonl").write_text(
            '{"id": "a", "nl": "n1", "difficulty": "easy"}\n'
            "\n"  # blank line ignored
            '{"id": "b", "nl": "n2", "difficulty": "hard"}\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(runner_mod, "EVAL_DIR", tmp_path)
        rows = runner_mod.load_icps()
        assert [r["id"] for r in rows] == ["a", "b"]
        assert rows[1]["difficulty"] == "hard"

    def test_missing_file_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(runner_mod, "EVAL_DIR", tmp_path)
        with pytest.raises(FileNotFoundError):
            runner_mod.load_icps()


# --- load_labels ---------------------------------------------------------


class TestLoadLabels:
    def test_missing_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(runner_mod, "EVAL_DIR", tmp_path)
        assert runner_mod.load_labels("icp-x") == {}

    def test_returns_siren_to_label(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        labels_dir = tmp_path / "labels"
        labels_dir.mkdir()
        (labels_dir / "icp-001.json").write_text(
            json.dumps({"siren_to_label": {"123456789": 2, "987654321": 1}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(runner_mod, "EVAL_DIR", tmp_path)
        labels = runner_mod.load_labels("icp-001")
        assert labels == {"123456789": 2, "987654321": 1}


# --- end-to-end with stub pipeline.run -----------------------------------


def _write_eval_fixtures(tmp_path: Path) -> None:
    """Lay out a tmp EVAL_DIR with two ICPs and one labeled."""
    (tmp_path / "icps.jsonl").write_text(
        '{"id": "icp-001", "nl": "paris saas", "difficulty": "easy"}\n'
        '{"id": "icp-002", "nl": "lyon consulting", "difficulty": "easy"}\n',
        encoding="utf-8",
    )
    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    (labels_dir / "icp-001.json").write_text(
        json.dumps(
            {
                "siren_to_label": {
                    "100000001": 2,
                    "100000002": 1,
                    "100000003": 0,
                }
            }
        ),
        encoding="utf-8",
    )


def _write_config(tmp_path: Path, name: str = "stub") -> Path:
    cfg_path = tmp_path / f"{name}.yaml"
    cfg_path.write_text(
        f"name: {name}\n"
        "parser_model: claude-haiku-4-5\n"
        "scorer_model: claude-haiku-4-5\n"
        "concurrency: 4\n"
        "top_n: 50\n",
        encoding="utf-8",
    )
    return cfg_path


def test_runner_writes_report_with_stub_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_eval_fixtures(tmp_path)
    cfg_path = _write_config(tmp_path)
    out_dir = tmp_path / "reports"

    captured_env: dict[str, dict[str, str | None]] = {}

    def stub_run(nl: str, *, output_dir: Any, top_n: int):
        # Record env at call time so we can assert scoped mutation took effect.
        captured_env[nl] = {
            "PROSPECTER_MODEL_PARSER": os.environ.get("PROSPECTER_MODEL_PARSER"),
            "PROSPECTER_MODEL_SCORER": os.environ.get("PROSPECTER_MODEL_SCORER"),
            "PROSPECTER_CONCURRENCY": os.environ.get("PROSPECTER_CONCURRENCY"),
        }
        leads = [_lead("100000001", 5), _lead("100000002", 4), _lead("100000003", 3)]
        return leads, _state(cost_cents=2.5)

    # Pin EVAL_DIR for label loading and freeze env mutations to this test.
    monkeypatch.setattr(runner_mod, "EVAL_DIR", tmp_path)
    # `pipeline` is lazy-imported inside `_run_one_icp`; patch on its
    # canonical module path so the deferred import resolves to the stub.
    import prospecter.pipeline as _pipeline_mod

    monkeypatch.setattr(_pipeline_mod, "run", stub_run)
    # Make sure prior process state can't shadow the scoped mutation.
    monkeypatch.delenv("PROSPECTER_MODEL_PARSER", raising=False)
    monkeypatch.delenv("PROSPECTER_MODEL_SCORER", raising=False)
    monkeypatch.delenv("PROSPECTER_CONCURRENCY", raising=False)

    rc = runner_mod.main(["--configs", str(cfg_path), "--out", str(out_dir), "--log", "WARNING"])
    assert rc == 0

    # Env got restored after the run.
    assert os.environ.get("PROSPECTER_MODEL_PARSER") is None
    assert os.environ.get("PROSPECTER_MODEL_SCORER") is None
    assert os.environ.get("PROSPECTER_CONCURRENCY") is None

    # Env was set inside the run.
    assert captured_env["paris saas"]["PROSPECTER_MODEL_PARSER"] == "claude-haiku-4-5"
    assert captured_env["paris saas"]["PROSPECTER_MODEL_SCORER"] == "claude-haiku-4-5"
    assert captured_env["paris saas"]["PROSPECTER_CONCURRENCY"] == "4"

    # Report file exists and has the expected shape.
    date_str = datetime.now(tz=UTC).date().isoformat()
    report_path = out_dir / f"{date_str}_stub.json"
    assert report_path.is_file()
    blob = json.loads(report_path.read_text(encoding="utf-8"))
    assert blob["config"]["name"] == "stub"
    assert len(blob["per_icp"]) == 2
    # icp-001 has labels: top-3 with labels 2, 1, 0 → P@10 = 2/10, NDCG > 0.
    icp1 = next(r for r in blob["per_icp"] if r["icp_id"] == "icp-001")
    assert icp1["error"] is None
    assert icp1["p_at_10"] == pytest.approx(2 / 10)
    assert icp1["ndcg_at_10"] > 0.0
    assert icp1["cost_usd"] == pytest.approx(0.025)
    # icp-002 has no labels → metrics fall to 0 with a warning.
    icp2 = next(r for r in blob["per_icp"] if r["icp_id"] == "icp-002")
    assert icp2["p_at_10"] == 0.0
    assert icp2["ndcg_at_10"] == 0.0

    # latest.json points at this report (symlink or copy, both fine).
    latest = out_dir / "latest.json"
    assert latest.exists()
    assert json.loads(latest.read_text(encoding="utf-8"))["config"]["name"] == "stub"


def test_runner_records_error_on_pipeline_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_eval_fixtures(tmp_path)
    cfg_path = _write_config(tmp_path, name="boom")
    out_dir = tmp_path / "reports"

    def stub_run(nl: str, *, output_dir: Any, top_n: int):
        raise RuntimeError("network down")

    monkeypatch.setattr(runner_mod, "EVAL_DIR", tmp_path)
    import prospecter.pipeline as _pipeline_mod

    monkeypatch.setattr(_pipeline_mod, "run", stub_run)

    rc = runner_mod.main(["--configs", str(cfg_path), "--out", str(out_dir), "--log", "CRITICAL"])
    assert rc == 0  # one ICP failure must not abort the eval

    date_str = datetime.now(tz=UTC).date().isoformat()
    report_path = out_dir / f"{date_str}_boom.json"
    blob = json.loads(report_path.read_text(encoding="utf-8"))
    assert all(r["error"] and "network down" in r["error"] for r in blob["per_icp"])
    assert blob["aggregate"]["p_at_10"] == 0.0
