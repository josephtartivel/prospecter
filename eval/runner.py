"""Eval runner — runs the pipeline over ``eval/icps.jsonl`` for one or
more configurations, writes a JSON report per configuration, and prints
a Rich summary table at the end.

Usage::

    uv run python -m eval.runner --configs eval/configs/*.yaml
    uv run python -m eval.runner --configs eval/configs/haiku.yaml
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from eval.metrics import aggregate, ndcg_at_k, precision_at_k

# `prospecter.pipeline` is imported lazily inside `_run_one_icp` rather
# than at module top. Importing it here would force-load `prospecter.llm`
# during pytest collection of `tests/test_runner.py`, which captures the
# original `LLM` class symbol; `tests/test_llm.py` then re-`importlib.reload`s
# `prospecter.llm`, leaving `pipeline` holding the stale class. Subsequent
# tests that monkeypatch `LLM.from_env` then patch the new class while
# the pipeline still calls the old one. Deferring the import sidesteps it.

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "eval"


# --- I/O helpers -----------------------------------------------------------


def load_icps() -> list[dict]:
    """Read ``eval/icps.jsonl`` into a list of dicts."""
    path = EVAL_DIR / "icps.jsonl"
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_labels(icp_id: str) -> dict[str, int]:
    """Read ``eval/labels/{icp_id}.json`` and return ``siren_to_label``.

    Missing labels file is not an error — eval over an unlabeled ICP
    surfaces zeros and a warning, which is the right signal during
    label-set bootstrap.
    """
    path = EVAL_DIR / "labels" / f"{icp_id}.json"
    if not path.is_file():
        log.warning("no labels for %s; metrics will be 0", icp_id)
        return {}
    blob = json.loads(path.read_text(encoding="utf-8"))
    return blob.get("siren_to_label", {})


# --- Per-config env scoping ------------------------------------------------


_CONFIG_ENV_KEYS: dict[str, str] = {
    # YAML key -> env var the agents already read.
    "parser_model": "PROSPECTER_MODEL_PARSER",
    "scorer_model": "PROSPECTER_MODEL_SCORER",
    "concurrency": "PROSPECTER_CONCURRENCY",
}


@contextmanager
def _scoped_env(updates: dict[str, str | None]) -> Iterator[None]:
    """Apply env updates for the duration of the block, then restore.

    A None value deletes the var. The restore runs in ``finally`` so a
    crash inside one config can't leak ``PROSPECTER_MODEL_*`` into the
    next config or into the surrounding pytest process.
    """
    saved = {k: os.environ.get(k) for k in updates}
    try:
        for k, v in updates.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, prior in saved.items():
            if prior is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prior


def _config_env_updates(cfg: dict[str, Any]) -> dict[str, str | None]:
    return {env: str(cfg[yml]) for yml, env in _CONFIG_ENV_KEYS.items() if yml in cfg}


# --- Per-ICP run ----------------------------------------------------------


def _run_one_icp(icp: dict[str, Any], *, top_n: int, csv_dir: Path) -> dict[str, Any]:
    """Run the pipeline for one ICP and return the per-row report entry."""
    icp_id = icp["id"]
    nl = icp["nl"]
    labels = load_labels(icp_id)

    from prospecter import pipeline  # see top-of-module comment

    t0 = time.perf_counter()
    error: str | None = None
    predicted: list[str] = []
    cost_usd = 0.0
    try:
        leads, state = pipeline.run(nl, output_dir=csv_dir, top_n=top_n)
        predicted = [lead.company.siren for lead in leads]
        cost_usd = float(state.cost_cents) / 100.0
    except Exception as e:  # noqa: BLE001 — we want to record any failure
        log.exception("pipeline failed on %s", icp_id)
        error = f"{type(e).__name__}: {e}"
    latency_ms = int((time.perf_counter() - t0) * 1000)

    p10 = precision_at_k(predicted, labels, k=10) if predicted else 0.0
    ndcg10 = ndcg_at_k(predicted, labels, k=10) if predicted else 0.0

    return {
        "icp_id": icp_id,
        "nl": nl,
        "difficulty": icp.get("difficulty"),
        "p_at_10": p10,
        "ndcg_at_10": ndcg10,
        "cost_usd": cost_usd,
        "latency_ms": latency_ms,
        "error": error,
    }


# --- Per-config run ------------------------------------------------------


def _run_one_config(
    cfg: dict[str, Any], icps: list[dict[str, Any]], *, out_dir: Path
) -> dict[str, Any]:
    name = cfg["name"]
    top_n = int(cfg.get("top_n", 50))
    csv_dir = out_dir / "csv" / name

    log.info(
        "running config: %s (parser=%s scorer=%s)",
        name,
        cfg.get("parser_model"),
        cfg.get("scorer_model"),
    )
    per_icp: list[dict[str, Any]] = []
    with _scoped_env(_config_env_updates(cfg)):
        for icp in icps:
            entry = _run_one_icp(icp, top_n=top_n, csv_dir=csv_dir / icp["id"])
            per_icp.append(entry)
            log.info(
                "  %s: P@10=%.2f NDCG@10=%.2f cost=$%.4f %dms %s",
                entry["icp_id"],
                entry["p_at_10"],
                entry["ndcg_at_10"],
                entry["cost_usd"],
                entry["latency_ms"],
                "ERROR" if entry["error"] else "",
            )

    stats = aggregate(per_icp)
    return {
        "config": cfg,
        "ran_at": datetime.now(tz=UTC).isoformat(),
        "per_icp": per_icp,
        "aggregate": dataclasses.asdict(stats),
    }


# --- Report I/O ----------------------------------------------------------


def _write_report(report: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now(tz=UTC).date().isoformat()
    name = report["config"]["name"]
    path = out_dir / f"{date}_{name}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    _update_latest(path)
    return path


def _update_latest(report_path: Path) -> None:
    """Point ``reports/latest.json`` at the newest report.

    Symlink first; on Windows or any filesystem that rejects symlinks,
    fall back to a plain copy. The dashboard reads ``latest.json`` so it
    must exist as a real readable file either way.
    """
    latest = report_path.parent / "latest.json"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
    except OSError:
        pass
    try:
        latest.symlink_to(report_path.name)
    except (OSError, NotImplementedError):
        shutil.copy2(report_path, latest)


# --- Summary table -------------------------------------------------------


def _summary_table(reports: list[dict[str, Any]]) -> Table:
    table = Table(title="Eval results — configs side-by-side")
    table.add_column("config")
    table.add_column("P@10", justify="right")
    table.add_column("NDCG@10", justify="right")
    table.add_column("$/run", justify="right")
    table.add_column("$/ICP", justify="right")
    table.add_column("p50 ms", justify="right")
    table.add_column("p95 ms", justify="right")
    table.add_column("errors", justify="right")

    for r in reports:
        agg = r["aggregate"]
        errors = sum(1 for row in r["per_icp"] if row.get("error"))
        table.add_row(
            r["config"]["name"],
            f"{agg['p_at_10']:.3f}",
            f"{agg['ndcg_at_10']:.3f}",
            f"${agg['cost_usd_total']:.4f}",
            f"${agg['cost_usd_mean']:.4f}",
            f"{agg['latency_p50_ms']:.0f}",
            f"{agg['latency_p95_ms']:.0f}",
            str(errors),
        )
    return table


# --- Entry point ---------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--configs", nargs="+", required=True, help="YAML config files.")
    p.add_argument("--out", default=None, help="Output directory (default: eval/reports).")
    p.add_argument("--log", default="INFO")
    args = p.parse_args(argv)

    # The CLI loads dotenv at its entry; the runner is a separate entry
    # point so it has to do it explicitly, otherwise model API keys in
    # `.env` never reach `os.environ` and every call 401s.
    load_dotenv()

    logging.basicConfig(
        level=args.log.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    out_dir = Path(args.out) if args.out else EVAL_DIR / "reports"

    icps = load_icps()
    log.info("loaded %d ICPs", len(icps))

    reports: list[dict[str, Any]] = []
    for cfg_path in args.configs:
        cfg = yaml.safe_load(Path(cfg_path).read_text(encoding="utf-8"))
        report = _run_one_config(cfg, icps, out_dir=out_dir)
        path = _write_report(report, out_dir)
        log.info("wrote %s", path)
        reports.append(report)

    Console().print(_summary_table(reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
