"""Tests for the eval metrics.

These functions are the basis of every claim in the README, so we exercise
edge cases (empty inputs, all-zero labels, partial overlaps) explicitly.
"""

from __future__ import annotations

import math

from eval.metrics import aggregate, ndcg_at_k, precision_at_k


class TestPrecisionAtK:
    def test_perfect_top_k(self):
        labels = {"a": 2, "b": 2, "c": 2}
        assert precision_at_k(["a", "b", "c"], labels, k=3) == 1.0

    def test_no_relevants_in_top_k(self):
        labels = {"a": 0, "b": 0, "c": 2}
        assert precision_at_k(["a", "b"], labels, k=2) == 0.0

    def test_unlabeled_treated_as_zero(self):
        labels = {"a": 2}
        assert precision_at_k(["a", "unknown"], labels, k=2) == 0.5

    def test_k_zero_returns_zero(self):
        assert precision_at_k(["a"], {"a": 2}, k=0) == 0.0

    def test_empty_ranking_returns_zero(self):
        assert precision_at_k([], {}, k=10) == 0.0


class TestNdcgAtK:
    def test_perfect_ranking_is_one(self):
        labels = {"a": 2, "b": 1, "c": 0}
        assert math.isclose(ndcg_at_k(["a", "b", "c"], labels, k=3), 1.0, rel_tol=1e-9)

    def test_reversed_ranking_below_one(self):
        labels = {"a": 2, "b": 1, "c": 0}
        assert ndcg_at_k(["c", "b", "a"], labels, k=3) < 1.0

    def test_all_zero_labels_return_zero(self):
        assert ndcg_at_k(["a", "b"], {"a": 0, "b": 0}, k=2) == 0.0

    def test_unlabeled_treated_as_zero(self):
        # Ideal would have placed "good" first; the actual ranking puts
        # unlabeled (=0) first, so NDCG is below 1 but above 0.
        labels = {"good": 2}
        result = ndcg_at_k(["unknown", "good"], labels, k=2)
        assert 0.0 < result < 1.0


class TestAggregate:
    def test_empty_returns_zeros(self):
        s = aggregate([])
        assert s.p_at_10 == 0.0
        assert s.cost_usd_total == 0.0

    def test_simple_aggregate(self):
        per_icp = [
            {"p_at_10": 0.6, "ndcg_at_10": 0.7, "cost_usd": 0.01, "latency_ms": 1000},
            {"p_at_10": 0.8, "ndcg_at_10": 0.9, "cost_usd": 0.02, "latency_ms": 2000},
        ]
        s = aggregate(per_icp)
        assert math.isclose(s.p_at_10, 0.7, rel_tol=1e-9)
        assert math.isclose(s.cost_usd_total, 0.03, rel_tol=1e-9)
        assert s.latency_p50_ms == 2000  # tiny sample, p50 picks index 1
