"""SIRENE store backed by DuckDB.

Wraps the SIRENE bulk dump in a DuckDB connection that exposes a single
``companies`` view (or table, after ``materialize()``) projecting only
the columns referenced by ``Company``. Reads CSVs by default; prefers
``sirene.parquet`` if it has been materialised.

See SPEC §5 and ``data/README.md``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)


def _quote_path(p: str | Path) -> str:
    """Inline a filesystem path as a SQL string literal.

    DuckDB's binder rejects prepared parameters inside table functions
    (``read_csv_auto``, ``read_parquet``) and inside ``COPY ... TO`` —
    the value must be a literal at parse time. Inlining is safe here
    because every path we pass originates from ``Path()`` and never from
    user input; the single-quote doubling defends against paths that
    legitimately contain apostrophes (e.g. ``/Users/joe's mac/...``).
    """
    return "'" + str(p).replace("'", "''") + "'"


# --- SIRENE tranche reference ------------------------------------------------
#
# ``trancheEffectifsUniteLegale`` is a small categorical of headcount
# brackets. Mapping each code to its (min, max, label) lets us express
# the ICP headcount range as a tranche-overlap predicate rather than
# strict inclusion.
#
# Source: INSEE — variable trancheEffectifsUniteLegale.
# https://www.sirene.fr/sirene/public/variable/trancheEffectifsUniteLegale
#
# ``"NN"`` is the conventional missing-value marker; the CSVs also emit
# blank fields, which we normalise to ``"NN"`` in the view.
TRANCHE_BOUNDS: dict[str, tuple[int | None, int | None, str]] = {
    "NN": (None, None, "unknown"),
    "00": (0, 0, "0"),
    "01": (1, 2, "1 to 2"),
    "02": (3, 5, "3 to 5"),
    "03": (6, 9, "6 to 9"),
    "11": (10, 19, "10 to 19"),
    "12": (20, 49, "20 to 49"),
    "21": (50, 99, "50 to 99"),
    "22": (100, 199, "100 to 199"),
    "31": (200, 249, "200 to 249"),
    "32": (250, 499, "250 to 499"),
    "41": (500, 999, "500 to 999"),
    "42": (1000, 1999, "1000 to 1999"),
    "51": (2000, 4999, "2000 to 4999"),
    "52": (5000, 9999, "5000 to 9999"),
    "53": (10000, None, "10000+"),
}


# --- INSEE department → region map ------------------------------------------
#
# SIRENE ships establishment communes (and therefore departments) but not
# region codes. The mapping is static for the lifetime of the bulk dump,
# so we ship it inline rather than load a CSV. INSEE region codes are the
# 2024 set (5-DOM regions kept).
DEPT_TO_REGION: dict[str, str] = {
    # Auvergne-Rhône-Alpes
    "01": "84",
    "03": "84",
    "07": "84",
    "15": "84",
    "26": "84",
    "38": "84",
    "42": "84",
    "43": "84",
    "63": "84",
    "69": "84",
    "73": "84",
    "74": "84",
    # Bourgogne-Franche-Comté
    "21": "27",
    "25": "27",
    "39": "27",
    "58": "27",
    "70": "27",
    "71": "27",
    "89": "27",
    "90": "27",
    # Bretagne
    "22": "53",
    "29": "53",
    "35": "53",
    "56": "53",
    # Centre-Val de Loire
    "18": "24",
    "28": "24",
    "36": "24",
    "37": "24",
    "41": "24",
    "45": "24",
    # Corse
    "2A": "94",
    "2B": "94",
    # Grand Est
    "08": "44",
    "10": "44",
    "51": "44",
    "52": "44",
    "54": "44",
    "55": "44",
    "57": "44",
    "67": "44",
    "68": "44",
    "88": "44",
    # Hauts-de-France
    "02": "32",
    "59": "32",
    "60": "32",
    "62": "32",
    "80": "32",
    # Île-de-France
    "75": "11",
    "77": "11",
    "78": "11",
    "91": "11",
    "92": "11",
    "93": "11",
    "94": "11",
    "95": "11",
    # Normandie
    "14": "28",
    "27": "28",
    "50": "28",
    "61": "28",
    "76": "28",
    # Nouvelle-Aquitaine
    "16": "75",
    "17": "75",
    "19": "75",
    "23": "75",
    "24": "75",
    "33": "75",
    "40": "75",
    "47": "75",
    "64": "75",
    "79": "75",
    "86": "75",
    "87": "75",
    # Occitanie
    "09": "76",
    "11": "76",
    "12": "76",
    "30": "76",
    "31": "76",
    "32": "76",
    "34": "76",
    "46": "76",
    "48": "76",
    "65": "76",
    "66": "76",
    "81": "76",
    "82": "76",
    # Pays de la Loire
    "44": "52",
    "49": "52",
    "53": "52",
    "72": "52",
    "85": "52",
    # Provence-Alpes-Côte d'Azur
    "04": "93",
    "05": "93",
    "06": "93",
    "13": "93",
    "83": "93",
    "84": "93",
    # DOM
    "971": "01",  # Guadeloupe
    "972": "02",  # Martinique
    "973": "03",  # Guyane
    "974": "04",  # La Réunion
    "976": "06",  # Mayotte
}


# Columns we project from each CSV. Listed here so the view definition
# below stays readable and so we can audit additions.
_COLS_UNITE_LEGALE = (
    "siren",
    "denominationUniteLegale",
    "activitePrincipaleUniteLegale",
    "trancheEffectifsUniteLegale",
    "dateCreationUniteLegale",
    "etatAdministratifUniteLegale",
    "statutDiffusionUniteLegale",
)
_COLS_ETABLISSEMENT = (
    "siren",
    "siret",
    "etablissementSiege",
    "codePostalEtablissement",
    "libelleCommuneEtablissement",
)


def _register_reference_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Install the static tranche and dept→region tables on ``con``.

    Idempotent — uses ``CREATE OR REPLACE``.
    """
    con.execute(
        """
        CREATE OR REPLACE TABLE _tranche (
            code VARCHAR,
            min_emp INTEGER,
            max_emp INTEGER,
            label VARCHAR
        )
        """
    )
    con.executemany(
        "INSERT INTO _tranche VALUES (?, ?, ?, ?)",
        [(code, lo, hi, label) for code, (lo, hi, label) in TRANCHE_BOUNDS.items()],
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE _dept_region (
            department_code VARCHAR,
            region_code VARCHAR
        )
        """
    )
    con.executemany(
        "INSERT INTO _dept_region VALUES (?, ?)",
        list(DEPT_TO_REGION.items()),
    )


def _install_companies_view_from_stocks(con: duckdb.DuckDBPyConnection) -> None:
    """Build the ``companies`` view from ``stock_unite_legale`` and ``stock_etablissement``.

    Assumes the two source views/tables and the reference tables already
    exist on ``con``. Splits this out so tests can populate the stock
    tables in memory and reuse the same projection logic the production
    path uses.

    The view restricts to:
      - the *siège* (headquarters) establishment per SIREN, which collapses
        the multi-row-per-SIREN issue,
      - public diffusion only (``statutDiffusionUniteLegale = 'O'``).

    Department code is derived from the first two characters of the postal
    code; for DOM postal codes (``97x..``) the first three are used.
    """
    con.execute(
        """
        CREATE OR REPLACE VIEW companies AS
        SELECT
            ul.siren                                           AS siren,
            et.siret                                           AS siret_main,
            COALESCE(NULLIF(ul.denominationUniteLegale, ''), '') AS name,
            ul.activitePrincipaleUniteLegale                   AS naf_code,
            COALESCE(NULLIF(ul.trancheEffectifsUniteLegale, ''), 'NN')
                                                               AS headcount_tranche,
            tr.label                                           AS headcount_label,
            tr.min_emp                                         AS tranche_min,
            tr.max_emp                                         AS tranche_max,
            CASE
                WHEN substr(et.codePostalEtablissement, 1, 2) = '97'
                    THEN substr(et.codePostalEtablissement, 1, 3)
                ELSE substr(et.codePostalEtablissement, 1, 2)
            END                                                AS department_code,
            COALESCE(dr.region_code, '')                       AS region_code,
            et.codePostalEtablissement                         AS postal_code,
            et.libelleCommuneEtablissement                     AS commune,
            TRY_CAST(ul.dateCreationUniteLegale AS DATE)       AS creation_date,
            (ul.etatAdministratifUniteLegale = 'A')            AS is_active
        FROM stock_unite_legale ul
        JOIN stock_etablissement et
            ON et.siren = ul.siren
           AND CAST(et.etablissementSiege AS VARCHAR) IN ('true', 'TRUE', '1')
        LEFT JOIN _tranche tr
            ON tr.code = COALESCE(NULLIF(ul.trancheEffectifsUniteLegale, ''), 'NN')
        LEFT JOIN _dept_region dr
            ON dr.department_code = CASE
                WHEN substr(et.codePostalEtablissement, 1, 2) = '97'
                    THEN substr(et.codePostalEtablissement, 1, 3)
                ELSE substr(et.codePostalEtablissement, 1, 2)
            END
        WHERE ul.statutDiffusionUniteLegale = 'O'
        """
    )


def _install_companies_view(con: duckdb.DuckDBPyConnection, base: Path) -> None:
    """Install the ``companies`` view on ``con``.

    Prefers ``base / sirene.parquet`` if it exists (materialised joined
    table — one fast scan). Falls back to reading the two CSVs and
    rebuilding the join.
    """
    parquet = base / "sirene.parquet"
    if parquet.exists():
        con.execute(
            f"CREATE OR REPLACE VIEW companies AS SELECT * FROM read_parquet({_quote_path(parquet)})"
        )
        return

    csv_unite = base / "StockUniteLegale_utf8.csv"
    csv_etab = base / "StockEtablissement_utf8.csv"
    if not csv_unite.exists() or not csv_etab.exists():
        raise FileNotFoundError(
            f"Expected SIRENE CSVs at {csv_unite} and {csv_etab}. "
            "Run scripts/fetch_sirene.sh — see data/README.md."
        )

    # Project at scan time (DuckDB pushes the column list into the CSV
    # reader, so unused columns are never materialised). The path is
    # inlined via ``_quote_path`` because the binder forbids prepared
    # parameters inside ``read_csv_auto``.
    #
    # ``quote='"'`` + ``escape='"'`` is the explicit RFC 4180 reading
    # (INSEE's stated convention). Without it, DuckDB's autodetector
    # samples the first ~20480 lines and concludes "no quote char" when
    # those rows are unquoted, then crashes on the first row with a
    # quoted internal comma (e.g. ``"SYND COPRO 5, RUE HEROLD"`` in
    # SIRENE 2026 line 38805). NOT ``ignore_errors`` / ``strict_mode=
    # false`` — those mask schema drift instead of fixing it.
    con.execute(
        f"""
        CREATE OR REPLACE VIEW stock_unite_legale AS
        SELECT {", ".join(_COLS_UNITE_LEGALE)}
        FROM read_csv_auto(
            {_quote_path(csv_unite)},
            header=true,
            all_varchar=true,
            quote='"',
            escape='"'
        )
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW stock_etablissement AS
        SELECT {", ".join(_COLS_ETABLISSEMENT)}
        FROM read_csv_auto(
            {_quote_path(csv_etab)},
            header=true,
            all_varchar=true,
            quote='"',
            escape='"'
        )
        """
    )
    _install_companies_view_from_stocks(con)


class SireneStore:
    """DuckDB connection over the SIRENE bulk dump.

    On ``connect()`` installs a ``companies`` view exposing the columns
    referenced by ``Company`` plus the tranche bounds used by Search.
    Tests can pass a pre-built connection via ``con=`` to skip file IO.
    """

    def __init__(
        self,
        base_path: str | Path | None = None,
        *,
        con: duckdb.DuckDBPyConnection | None = None,
    ) -> None:
        self.base = Path(base_path) if base_path is not None else Path("data/sirene")
        self._con = con

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._con is not None:
            return self._con
        if not self.base.exists():
            raise FileNotFoundError(
                f"SIRENE store not found at {self.base}. "
                "Run scripts/fetch_sirene.sh — see data/README.md."
            )
        con = duckdb.connect()
        _register_reference_tables(con)
        _install_companies_view(con, self.base)
        self._con = con
        return con

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    @classmethod
    def materialize(cls, base_path: str | Path | None = None) -> Path:
        """Write the joined ``companies`` view to ``sirene.parquet``.

        Idempotent: skips work if the parquet exists and is at least as
        new as both source CSVs. Delete the parquet to force a rebuild.
        Returns the parquet path.
        """
        base = Path(base_path) if base_path is not None else Path("data/sirene")
        parquet = base / "sirene.parquet"
        csv_unite = base / "StockUniteLegale_utf8.csv"
        csv_etab = base / "StockEtablissement_utf8.csv"

        if parquet.exists():
            pq_mtime = parquet.stat().st_mtime
            if (
                csv_unite.exists()
                and csv_etab.exists()
                and pq_mtime >= csv_unite.stat().st_mtime
                and pq_mtime >= csv_etab.stat().st_mtime
            ):
                log.info("sirene.parquet is up to date; skipping materialize")
                return parquet

        if not csv_unite.exists() or not csv_etab.exists():
            raise FileNotFoundError(
                f"Cannot materialise: missing CSVs at {csv_unite} or {csv_etab}."
            )

        con = duckdb.connect()
        try:
            _register_reference_tables(con)
            _install_companies_view(con, base)
            # ``COPY ... TO`` shares the table-function binder restriction:
            # the destination must be a string literal, not a prepared param.
            con.execute(
                f"COPY companies TO {_quote_path(parquet)} (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        finally:
            con.close()
        log.info("wrote %s", parquet)
        return parquet


# Entry point for ``uv run python -m prospecter.tools.duckdb_tool materialize``,
# the command documented in ``data/README.md``. Kept as a tiny typer app
# rather than wired into the main ``prospecter`` CLI to keep the materialise
# operation co-located with the store it operates on.
if __name__ == "__main__":
    import typer

    _app = typer.Typer(add_completion=False)

    @_app.callback()
    def _root() -> None:
        """SIRENE store maintenance commands."""
        # Presence of a callback forces typer into multi-command
        # dispatch. Without it, a single-command Typer app collapses
        # to "options-only" mode and the subcommand name gets parsed
        # as an unexpected positional argument.

    @_app.command()
    def materialize(base: str = "data/sirene") -> None:
        """Write the joined companies view to ``<base>/sirene.parquet``."""
        SireneStore.materialize(base)

    _app()
