"""Tests for the deterministic Search agent.

Builds an in-memory DuckDB ``companies`` view from ~30 hand-crafted
SIRENE-shaped rows so the WHERE-clause logic, tranche-overlap predicate,
ordering, and truncation behaviour can be exercised end-to-end without
the bulk dump.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import duckdb
import pytest

from prospecter.agents.search import search
from prospecter.schemas import ICP
from prospecter.tools.duckdb_tool import (
    SireneStore,
    _install_companies_view_from_stocks,
    _register_reference_tables,
)


@pytest.fixture
def store():
    """30-row in-memory SireneStore.

    The fixture installs the production ``companies`` view over raw
    stock tables, so the view's casting, joins, and filters (siège
    selection, public-diffusion filter) are exercised alongside
    ``search``'s WHERE-clause logic.
    """
    con = duckdb.connect()

    con.execute(
        """
        CREATE TABLE stock_unite_legale (
            siren VARCHAR,
            denominationUniteLegale VARCHAR,
            activitePrincipaleUniteLegale VARCHAR,
            trancheEffectifsUniteLegale VARCHAR,
            dateCreationUniteLegale VARCHAR,
            etatAdministratifUniteLegale VARCHAR,
            statutDiffusionUniteLegale VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE stock_etablissement (
            siren VARCHAR,
            siret VARCHAR,
            etablissementSiege VARCHAR,
            codePostalEtablissement VARCHAR,
            libelleCommuneEtablissement VARCHAR
        )
        """
    )

    # One date computed at runtime so the age-window tests are stable
    # regardless of when the suite is run. All other dates are fixed and
    # safely older than any plausible age-max window.
    today = date.today()
    recent = (today - timedelta(days=30)).isoformat()

    rows_ul = [
        # (siren, name, naf, tranche, creation_date, etat, diffusion)
        ("000000001", "alpha sas", "62.02A", "11", "2022-03-01", "A", "O"),
        ("000000002", "beta sas", "62.02A", "12", "2021-06-15", "A", "O"),
        ("000000003", "gamma sas", "62.02A", "21", "2020-01-10", "A", "O"),
        ("000000004", "delta sas", "62.02A", "31", "2018-09-22", "A", "O"),
        ("000000005", "epsilon sas", "62.01Z", "02", "2023-05-15", "A", "O"),
        ("000000006", "zeta sas", "62.01Z", "11", "2022-11-30", "A", "O"),
        ("000000007", "eta sas", "62.01Z", "53", "2010-01-05", "A", "O"),
        ("000000008", "theta sas", "56.10A", "00", "2023-05-01", "A", "O"),
        ("000000009", "iota sas", "56.10A", "01", "2022-08-12", "A", "O"),
        ("000000010", "kappa sas", "56.10A", "12", "2019-03-20", "A", "O"),
        ("000000011", "lambda sas", "56.10A", "11", "2021-12-01", "A", "O"),
        ("000000012", "mu sas", "70.22Z", "11", "2022-01-15", "A", "O"),
        ("000000013", "nu sas", "70.22Z", "12", "2017-04-22", "A", "O"),
        ("000000014", "xi sas", "70.22Z", "NN", "2020-06-01", "A", "O"),
        ("000000015", "omicron sas", "62.02A", "12", "2015-09-09", "C", "O"),  # ceased
        ("000000016", "pi sas", "62.02A", "11", "2016-07-07", "C", "O"),  # ceased
        ("000000017", "rho sas", "62.02A", "11", "2015-01-01", "A", "P"),  # restricted
        ("000000018", "sigma sas", "62.02A", "11", "2014-06-01", "A", "N"),  # restricted
        ("000000019", "tau sas", "62.02A", "41", "2017-02-15", "A", "O"),
        ("000000020", "upsilon sas", "62.02A", "11", recent, "A", "O"),
        ("000000021", "phi sas", "62.02A", "12", "2018-06-15", "A", "O"),
        ("000000022", "chi sas", "62.02A", "11", "2014-01-01", "A", "O"),
        ("000000023", "psi sas", "56.10A", "11", "2022-02-22", "A", "O"),
        ("000000024", "omega sas", "70.22Z", "12", "2020-10-10", "A", "O"),
        ("000000025", "alpha2 sas", "62.01Z", "12", "2023-08-15", "A", "O"),
        ("000000026", "beta2 sas", "62.02A", "21", "2020-03-01", "A", "O"),
        ("000000027", "gamma2 sas", "62.02A", "31", "2020-03-15", "A", "O"),
        ("000000028", "delta2 sas", "62.02A", "32", "2020-03-20", "A", "O"),
        ("000000029", "epsilon2 sas", "62.02A", "41", "2020-03-25", "A", "O"),
        ("000000030", "zeta2 sas", "62.02A", "12", "2020-04-10", "A", "O"),
    ]
    con.executemany(
        "INSERT INTO stock_unite_legale VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows_ul,
    )

    # One siège per SIREN; SIREN 1 also has a secondary establishment in
    # a different postal code, so the view's "siège only" join collapses
    # the multi-row case and the secondary's address never leaks into
    # results for that SIREN.
    rows_et = [
        ("000000001", "00000000100001", "true", "75001", "Paris"),
        ("000000001", "00000000100002", "false", "75011", "Paris"),
        ("000000002", "00000000200001", "true", "75002", "Paris"),
        ("000000003", "00000000300001", "true", "92100", "Boulogne-Billancourt"),
        ("000000004", "00000000400001", "true", "92200", "Neuilly-sur-Seine"),
        ("000000005", "00000000500001", "true", "75011", "Paris"),
        ("000000006", "00000000600001", "true", "75011", "Paris"),
        ("000000007", "00000000700001", "true", "75008", "Paris"),
        ("000000008", "00000000800001", "true", "69001", "Lyon"),
        ("000000009", "00000000900001", "true", "69002", "Lyon"),
        ("000000010", "00000001000001", "true", "69003", "Lyon"),
        ("000000011", "00000001100001", "true", "13001", "Marseille"),
        ("000000012", "00000001200001", "true", "33000", "Bordeaux"),
        ("000000013", "00000001300001", "true", "33000", "Bordeaux"),
        ("000000014", "00000001400001", "true", "75003", "Paris"),
        ("000000015", "00000001500001", "true", "75004", "Paris"),
        ("000000016", "00000001600001", "true", "75005", "Paris"),
        ("000000017", "00000001700001", "true", "75001", "Paris"),
        ("000000018", "00000001800001", "true", "75001", "Paris"),
        ("000000019", "00000001900001", "true", "92300", "Levallois-Perret"),
        ("000000020", "00000002000001", "true", "75009", "Paris"),
        ("000000021", "00000002100001", "true", "75010", "Paris"),
        ("000000022", "00000002200001", "true", "75006", "Paris"),
        ("000000023", "00000002300001", "true", "13002", "Marseille"),
        ("000000024", "00000002400001", "true", "13003", "Marseille"),
        ("000000025", "00000002500001", "true", "33100", "Bordeaux"),
        ("000000026", "00000002600001", "true", "75001", "Paris"),
        ("000000027", "00000002700001", "true", "75002", "Paris"),
        ("000000028", "00000002800001", "true", "75003", "Paris"),
        ("000000029", "00000002900001", "true", "75004", "Paris"),
        ("000000030", "00000003000001", "true", "75007", "Paris"),
    ]
    con.executemany(
        "INSERT INTO stock_etablissement VALUES (?, ?, ?, ?, ?)",
        rows_et,
    )

    _register_reference_tables(con)
    _install_companies_view_from_stocks(con)
    return SireneStore(con=con)


def _sirens(rows):
    return sorted(c.siren for c in rows)


class TestNAFFilter:
    def test_single_naf_returns_only_matching_active_public(self, store):
        # 62.02A rows: 1,2,3,4,15,16,17,18,19,20,21,22,26-30 (17 total).
        # Drop 2 ceased + 2 restricted → 13.
        results = search(ICP(naf_codes=["62.02A"]), store=store)
        assert len(results) == 13
        assert {c.naf_code for c in results} == {"62.02A"}

    def test_multi_naf_union(self, store):
        # 62.01Z active+public: rows 5, 6, 7, 25.
        results = search(ICP(naf_codes=["62.02A", "62.01Z"]), store=store)
        assert len(results) == 17

    def test_unknown_naf_returns_empty(self, store):
        results = search(ICP(naf_codes=["99.99Z"]), store=store)
        assert results == []

    def test_full_code_still_exact(self, store):
        # Regression-lock: when the parser produces a complete 6-char
        # NAF code, the prefix predicate must self-match it (data is
        # exactly 6 chars wide so `LIKE 'XXXXXX%'` ≡ `=`).
        results = search(ICP(naf_codes=["62.02A"]), store=store)
        assert {c.naf_code for c in results} == {"62.02A"}
        assert len(results) == 13

    def test_prefix_expands_subletters(self, store):
        # `62.0` matches 62.02A and 62.01Z — the whole 62.* division.
        results = search(ICP(naf_codes=["62.0"]), store=store)
        assert {c.naf_code for c in results} == {"62.02A", "62.01Z"}
        assert len(results) == 13 + 4

    def test_short_prefix_two_digit(self, store):
        # `62` is the shortest meaningful NAF prefix (division level).
        results = search(ICP(naf_codes=["62"]), store=store)
        assert {c.naf_code for c in results} == {"62.02A", "62.01Z"}

    def test_mixed_full_and_prefix(self, store):
        # Caller can mix prefixes and full codes in one query.
        results = search(ICP(naf_codes=["56.10", "62.01Z"]), store=store)
        nafs = {c.naf_code for c in results}
        assert nafs == {"56.10A", "62.01Z"}

    def test_nonexistent_z_code_returns_zero(self, store):
        # `62.02Z` is the NACE-style spelling Mistral sometimes emits;
        # there is no such code in NAF/SIRENE so the result is 0. This
        # test documents the failure mode as a *parser* bug — the
        # search-side fix intentionally does not mask it.
        results = search(ICP(naf_codes=["62.02Z"]), store=store)
        assert results == []


class TestHeadcountTrancheOverlap:
    def test_range_inside_two_tranches(self, store):
        # ICP [10,49] overlaps tranche 11 (10-19) and 12 (20-49) only.
        results = search(
            ICP(naf_codes=["62.02A"], headcount_min=10, headcount_max=49),
            store=store,
        )
        assert _sirens(results) == [
            "000000001",
            "000000002",
            "000000020",
            "000000021",
            "000000022",
            "000000030",
        ]

    def test_min_only_keeps_open_ended_tranche(self, store):
        # ICP min=5000 → only tranches whose max >= 5000. Among 62.01Z
        # rows that's just tranche 53 (10000+).
        results = search(
            ICP(naf_codes=["62.01Z"], headcount_min=5000),
            store=store,
        )
        assert _sirens(results) == ["000000007"]

    def test_max_only_excludes_high_tranches(self, store):
        results = search(
            ICP(naf_codes=["62.02A"], headcount_max=49),
            store=store,
        )
        assert _sirens(results) == [
            "000000001",
            "000000002",
            "000000020",
            "000000021",
            "000000022",
            "000000030",
        ]

    def test_unknown_tranche_passes_when_filter_set(self, store):
        # Tranche "NN" has NULL bounds; we keep these under uncertainty
        # rather than excluding them (better recall).
        results = search(
            ICP(naf_codes=["70.22Z"], headcount_min=100, headcount_max=200),
            store=store,
        )
        assert "000000014" in _sirens(results)


class TestGeographyFilters:
    def test_region_filter(self, store):
        # Region 84 = Auvergne-Rhône-Alpes; dept 69 (Lyon) maps there.
        results = search(
            ICP(naf_codes=["56.10A"], region_code="84"),
            store=store,
        )
        assert _sirens(results) == ["000000008", "000000009", "000000010"]

    def test_department_filter(self, store):
        results = search(
            ICP(naf_codes=["56.10A"], department_codes=["13"]),
            store=store,
        )
        assert _sirens(results) == ["000000011", "000000023"]

    def test_postal_filter(self, store):
        # rows 17,18 share 75001 but are diffusion-restricted; they must
        # not appear.
        results = search(
            ICP(naf_codes=["62.02A"], postal_codes=["75001"]),
            store=store,
        )
        assert _sirens(results) == ["000000001", "000000026"]

    def test_postal_prefix_expands_to_arrondissement_set(self, store):
        # `["75"]` matches every Paris arrondissement in the fixture.
        # Same prefix-tolerant predicate as NAF: 5-char postal code
        # data means `LIKE 'XXXXX%'` self-matches a full code (covered
        # by ``test_postal_filter`` above as the regression-lock).
        results = search(ICP(postal_codes=["75"]), store=store)
        sirens = _sirens(results)
        # 14 Paris siège rows after active+public filters (see fixture).
        assert len(sirens) == 14
        assert all(c.postal_code.startswith("75") for c in results)

    def test_only_siege_establishment_used(self, store):
        # SIREN 1 has a non-siège establishment at 75011, but only its
        # siège (75001) should be the row in `companies`. So a search at
        # 75011 must not return SIREN 1 — only SIRENs whose siège is at
        # 75011 (5 and 6).
        results = search(
            ICP(naf_codes=["62.02A", "62.01Z"], postal_codes=["75011"]),
            store=store,
        )
        sirens = _sirens(results)
        assert "000000001" not in sirens
        assert "000000005" in sirens
        assert "000000006" in sirens


class TestAgeFilters:
    def test_age_max_months_keeps_recent_only(self, store):
        # Cutoff = today - 2 months. Only the row with `recent` =
        # today - 30d qualifies; every fixed date is much older.
        results = search(
            ICP(naf_codes=["62.02A"], age_max_months=2),
            store=store,
        )
        assert _sirens(results) == ["000000020"]

    def test_age_min_months_excludes_recent(self, store):
        results = search(
            ICP(naf_codes=["62.02A"], age_min_months=2),
            store=store,
        )
        sirens = _sirens(results)
        assert "000000020" not in sirens
        assert len(sirens) == 12


class TestActiveFilter:
    def test_default_excludes_ceased(self, store):
        sirens = _sirens(search(ICP(naf_codes=["62.02A"]), store=store))
        assert "000000015" not in sirens
        assert "000000016" not in sirens

    def test_require_active_false_includes_ceased(self, store):
        results = search(
            ICP(naf_codes=["62.02A"], require_active=False),
            store=store,
        )
        sirens = _sirens(results)
        assert {"000000015", "000000016"}.issubset(sirens)
        # Restricted rows are filtered by the view, not the active flag,
        # so they remain excluded.
        assert "000000017" not in sirens
        assert "000000018" not in sirens


class TestStatutDiffusion:
    def test_restricted_rows_always_excluded(self, store):
        results = search(
            ICP(naf_codes=["62.02A"], require_active=False),
            store=store,
        )
        sirens = _sirens(results)
        assert "000000017" not in sirens
        assert "000000018" not in sirens


class TestOrderingAndTruncation:
    def test_sort_creation_date_desc(self, store):
        results = search(ICP(naf_codes=["62.02A"]), store=store)
        dates = [c.creation_date for c in results]
        assert dates == sorted(dates, reverse=True)

    def test_max_results_truncates_and_warns(self, store, caplog):
        with caplog.at_level(logging.WARNING):
            results = search(
                ICP(naf_codes=["62.02A"]),
                store=store,
                max_results=5,
            )
        assert len(results) == 5
        assert any("truncated" in r.message for r in caplog.records)

    def test_no_warning_under_cap(self, store, caplog):
        with caplog.at_level(logging.WARNING):
            search(ICP(naf_codes=["62.01Z"]), store=store, max_results=100)
        assert not any("truncated" in r.message for r in caplog.records)
