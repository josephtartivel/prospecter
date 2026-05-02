"""Eval metrics: precision@K, NDCG@K, cost stats.

These are the deterministic parts of the eval. Unit-tested in
`tests/test_metrics.py`.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass
class RunStats:
    """Aggregate stats across a list of per-ICP results."""

    p_at_10: float
    ndcg_at_10: float
    cost_usd_total: float
    cost_usd_mean: float
    latency_p50_ms: float
    latency_p95_ms: float


def precision_at_k(predicted_ranking: Sequence[str], labels: dict[str, int], k: int = 10) -> float:
    """Fraction of top-K with label ≥ 1. Unlabeled items are treated as 0."""
    if k <= 0 or not predicted_ranking:
        return 0.0
    top_k = predicted_ranking[:k]
    relevant = sum(1 for siren in top_k if labels.get(siren, 0) >= 1)
    return relevant / k


def ndcg_at_k(predicted_ranking: Sequence[str], labels: dict[str, int], k: int = 10) -> float:
    """Standard NDCG@K with linear gains. Unlabeled items are treated as 0."""
    if k <= 0 or not predicted_ranking:
        return 0.0

    def dcg(gains: Sequence[int]) -> float:
        # rank starts at 1 → discount at i+2 because i is 0-indexed
        return sum(g / math.log2(i + 2) for i, g in enumerate(gains))

    top_k_gains = [labels.get(siren, 0) for siren in predicted_ranking[:k]]
    ideal_gains = sorted(labels.values(), reverse=True)[:k]
    actual = dcg(top_k_gains)
    ideal = dcg(ideal_gains)
    if ideal == 0:
        return 0.0
    return actual / ideal


def aggregate(per_icp: list[dict]) -> RunStats:
    """Aggregate per-ICP results into a single `RunStats`.

    Each entry in `per_icp` should have the keys: `p_at_10`, `ndcg_at_10`,
    `cost_usd`, `latency_ms`.
    """
    if not per_icp:
        return RunStats(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    p10 = statistics.mean(r["p_at_10"] for r in per_icp)
    ndcg = statistics.mean(r["ndcg_at_10"] for r in per_icp)
    cost_total = sum(r["cost_usd"] for r in per_icp)
    cost_mean = statistics.mean(r["cost_usd"] for r in per_icp)
    lats = sorted(r["latency_ms"] for r in per_icp)
    p50 = lats[len(lats) // 2]
    p95 = lats[max(0, int(0.95 * (len(lats) - 1)))]
    return RunStats(
        p_at_10=p10,
        ndcg_at_10=ndcg,
        cost_usd_total=cost_total,
        cost_usd_mean=cost_mean,
        latency_p50_ms=float(p50),
        latency_p95_ms=float(p95),
    )
