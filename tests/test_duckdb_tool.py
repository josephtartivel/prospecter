"""Regression tests for ``duckdb_tool`` path-handling.

DuckDB's binder rejects prepared parameters inside table functions
(``read_csv_auto``, ``read_parquet``) and inside ``COPY ... TO``. The
production code originally used ``$path`` placeholders at four sites
and crashed on a fresh install with ``BinderException: Unexpected
prepared parameter``. These tests exercise the patched path-inlining
helper end-to-end against a real (but tiny) on-disk dataset, so a
regression to prepared-param style would be caught immediately.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from prospecter.tools.duckdb_tool import (
    SireneStore,
    _install_companies_view,
    _register_reference_tables,
)

# Header schemas for the SIRENE CSVs, projected down to the columns the
# view actually reads. Keeping the headers in one place so both tests
# (CSV-direct and parquet round-trip) write the same shape.
_UL_HEADER = (
    "siren,denominationUniteLegale,activitePrincipaleUniteLegale,"
    "trancheEffectifsUniteLegale,dateCreationUniteLegale,"
    "etatAdministratifUniteLegale,statutDiffusionUniteLegale"
)
_ET_HEADER = "siren,siret,etablissementSiege,codePostalEtablissement,libelleCommuneEtablissement"


def _write_minimal_csvs(base: Path) -> None:
    """Drop two 1-row SIRENE-shaped CSVs into ``base``.

    The row matches a typical Paris SaaS startup so any downstream
    assertion about ``companies`` columns has stable values.
    """
    base.mkdir(parents=True, exist_ok=True)
    (base / "StockUniteLegale_utf8.csv").write_text(
        f"{_UL_HEADER}\n000000001,acme sas,62.02A,11,2023-01-15,A,O\n",
        encoding="utf-8",
    )
    (base / "StockEtablissement_utf8.csv").write_text(
        f"{_ET_HEADER}\n000000001,00000000100001,true,75001,Paris\n",
        encoding="utf-8",
    )


def test_install_companies_view_reads_csv(tmp_path: Path) -> None:
    """Hits the ``read_csv_auto`` branch — the exact site that raised
    ``BinderException`` before the fix. Without the path-inlining
    helper this call crashes inside ``con.execute``."""
    _write_minimal_csvs(tmp_path)

    con = duckdb.connect()
    _register_reference_tables(con)
    _install_companies_view(con, tmp_path)

    rows = con.execute(
        "SELECT siren, name, naf_code, headcount_label, postal_code FROM companies"
    ).fetchall()

    assert rows == [("000000001", "acme sas", "62.02A", "10 to 19", "75001")]


def test_materialize_round_trip_reads_parquet(tmp_path: Path) -> None:
    """Hits the ``COPY ... TO`` and ``read_parquet`` branches. Materialise
    writes the parquet, then a fresh ``connect()`` must read it back via
    the parquet branch of ``_install_companies_view``."""
    _write_minimal_csvs(tmp_path)

    parquet_path = SireneStore.materialize(tmp_path)
    assert parquet_path == tmp_path / "sirene.parquet"
    assert parquet_path.exists()

    store = SireneStore(tmp_path)
    con = store.connect()
    try:
        siren = con.execute("SELECT siren FROM companies").fetchone()
        assert siren == ("000000001",)
    finally:
        store.close()
