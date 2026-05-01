"""Search agent — ICP → list[Company] via DuckDB.

Deterministic, no LLM. The agent is a thin function that translates ICP
fields into a parameterized DuckDB query, runs it against the SIRENE
views, and returns up to `max_results` Company rows.

Skeleton — see SPEC §7 for the contract. Implement in the second build
session (PROMPTS.md, session 2).
"""

from __future__ import annotations

import logging

from prospecter.schemas import Company, ICP
from prospecter.tools.duckdb_tool import SireneStore

log = logging.getLogger(__name__)


def search(icp: ICP, *, store: SireneStore, max_results: int = 1000) -> list[Company]:
    """Return up to `max_results` candidates matching the ICP.

    Sorted by `creation_date DESC` so newer companies surface first.
    Truncates with a logged warning if the unbounded count exceeds
    `max_results`.

    TODO(session-2):
      - Build the WHERE clause incrementally from non-empty ICP fields.
      - Project only the columns Company needs.
      - Map SIRENE tranche codes to the (min, max) pair so headcount filters
        work as expected.
      - Use parameterized queries (DuckDB Python supports `$param` style).
    """
    raise NotImplementedError("implement in session 2 (see PROMPTS.md)")
