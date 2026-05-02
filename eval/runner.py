"""Eval runner — runs the pipeline over `eval/icps.jsonl` for one or more
configurations, writes a JSON report, and prints a summary table.

Skeleton — implement in session 6 once the pipeline is wired end-to-end.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "eval"


def load_icps() -> list[dict]:
    """Read `eval/icps.jsonl` into a list of dicts."""
    path = EVAL_DIR / "icps.jsonl"
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_labels(icp_id: str) -> dict[str, int]:
    """Read `eval/labels/{icp_id}.json` and return `siren_to_label`."""
    path = EVAL_DIR / "labels" / f"{icp_id}.json"
    if not path.is_file():
        log.warning("no labels for %s; metrics will be 0", icp_id)
        return {}
    blob = json.loads(path.read_text(encoding="utf-8"))
    return blob.get("siren_to_label", {})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--configs", nargs="+", required=True, help="YAML config files.")
    p.add_argument("--out", default=str(EVAL_DIR / "reports"), help="Output directory.")
    p.add_argument("--log", default="INFO")
    args = p.parse_args(argv)

    logging.basicConfig(level=args.log.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    icps = load_icps()
    log.info("loaded %d ICPs", len(icps))

    for cfg_path in args.configs:
        cfg = yaml.safe_load(Path(cfg_path).read_text(encoding="utf-8"))
        log.info("running config: %s", cfg["name"])
        # TODO(session-6): for each ICP:
        #   - set env vars per cfg (parser/scorer model)
        #   - run pipeline.run(icp["nl"])
        #   - compute precision_at_k, ndcg_at_k against load_labels(icp["id"])
        #   - record cost_usd, latency_ms
        # Then aggregate and write `eval/reports/{date}_{name}.json`.
        raise NotImplementedError("implement in session 6")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
