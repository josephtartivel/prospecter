"""SIRENE store backed by DuckDB.

Wraps the SIRENE bulk dump in a `DuckDBPyConnection` with two views:
`unite_legale` and `etablissement`. Knows how to materialise to Parquet
for faster repeat scans.

Skeleton — see SPEC §5 and `data/README.md`. Implement in session 2.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)


class SireneStore:
    """Lazy DuckDB connection over the SIRENE CSV (or Parquet if present)."""

    def __init__(self, base_path: str | Path | None = None) -> None:
        self.base = Path(base_path or os.environ.get("PROSPECTER_SIRENE_PATH", "data/sirene"))
        self._con: duckdb.DuckDBPyConnection | None = None

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._con is not None:
            return self._con
        if not self.base.exists():
            raise FileNotFoundError(
                f"SIRENE store not found at {self.base}. "
                "Run scripts/fetch_sirene.sh — see data/README.md."
            )
        con = duckdb.connect()
        # TODO(session-2): create views over the CSV files with a column
        # projection (only the ~15 columns we use). Prefer Parquet if
        # `sirene.parquet` exists.
        self._con = con
        return con

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None
