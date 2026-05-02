"""Tests for the Pydantic schemas.

Schemas are the contract between every component, so it's worth pinning
the validation behaviour explicitly. We don't test every field — we test
the rules that prevent bad runs (empty ICP, inverted ranges).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prospecter.schemas import ICP, Score


class TestICPValidation:
    def test_at_least_one_filter_required(self):
        with pytest.raises(ValidationError) as ei:
            ICP()  # all defaults — empty
        assert "at least one" in str(ei.value).lower()

    def test_naf_codes_alone_is_enough(self):
        icp = ICP(naf_codes=["56.10A"])
        assert icp.naf_codes == ["56.10A"]

    def test_headcount_min_alone_is_enough(self):
        icp = ICP(headcount_min=10)
        assert icp.headcount_min == 10

    def test_inverted_headcount_rejected(self):
        with pytest.raises(ValidationError):
            ICP(headcount_min=100, headcount_max=10)

    def test_inverted_age_rejected(self):
        with pytest.raises(ValidationError):
            ICP(age_min_months=120, age_max_months=12)

    def test_negative_headcount_rejected(self):
        with pytest.raises(ValidationError):
            ICP(headcount_min=-1)


class TestScoreValidation:
    def test_value_outside_range_rejected(self):
        with pytest.raises(ValidationError):
            Score(siren="123456789", value=6, reason="x", confidence=1.0)
        with pytest.raises(ValidationError):
            Score(siren="123456789", value=0, reason="x", confidence=1.0)

    def test_confidence_outside_unit_rejected(self):
        with pytest.raises(ValidationError):
            Score(siren="123456789", value=3, reason="x", confidence=1.5)

    def test_short_siren_rejected(self):
        with pytest.raises(ValidationError):
            Score(siren="123", value=3, reason="x", confidence=0.9)

    def test_reason_truncation_enforced(self):
        with pytest.raises(ValidationError):
            Score(siren="123456789", value=3, reason="x" * 250, confidence=0.9)
