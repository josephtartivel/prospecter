"""Search agent — ICP → list[Company] via DuckDB.

Deterministic, no LLM. Translates the structured ICP into a parameterised
DuckDB query against the ``companies`` view installed by ``SireneStore``,
sorts by ``creation_date DESC``, and caps the result list at
``max_results``.

See SPEC §7 for the contract.
"""

from __future__ import annotations

import logging
import re

from prospecter.schemas import ICP, Company
from prospecter.tools.duckdb_tool import SireneStore

log = logging.getLogger(__name__)


_NACE_Z = re.compile(r"^\d{2}\.\d{2}Z$")


def _normalize_naf_code(code: str) -> str:
    """Rewrite a ``XX.XXZ`` code to its 5-char prefix.

    ``Z`` in this position is ambiguous: legitimate monolithic NAF
    (``62.01Z``, ``70.22Z``) or NACE Rev2 contamination from the parser
    (``10.71Z`` does not exist in NAF; the subdivision is
    ``10.71A/B/C/D``). Stripping the ``Z`` is symmetric: legitimate
    monolithic codes still match because their parent prefix has no
    other NAF children; NACE-erroneous codes expand to the real NAF
    subdivision. See ADR-009.
    """
    return code[:-1] if _NACE_Z.fullmatch(code) else code


# The SELECT list matches the field order of ``Company`` so we can pass
# the cursor's row tuple straight into the model. Tranche bounds are
# carried on the view but never selected — they exist only to power the
# headcount-overlap WHERE clause.
_SELECT = """
SELECT
    siren,
    siret_main,
    name,
    naf_code,
    headcount_tranche,
    headcount_label,
    region_code,
    department_code,
    postal_code,
    commune,
    creation_date,
    is_active
FROM companies
"""


def _prefix_or_clause(
    field: str, values: list[str], params: dict[str, object], key_prefix: str
) -> str:
    """Build ``(field LIKE $k0 OR field LIKE $k1 ...)`` with ``v || '%'`` params.

    Prefix-tolerant predicate over a hierarchical-code column. The LLM-
    typed inputs (NAF, postal) are taxonomies where a partial code is a
    valid generalisation: ``10.71`` covers all sub-letters, ``75`` covers
    every Paris arrondissement. Full-code data is fixed-width (NAF=6,
    postal=5), so a complete value's ``LIKE 'XXXXXX%'`` matches only
    itself — the predicate is backward-compatible with the ``IN(...)``
    formulation it replaces. See ADR-008.
    """
    parts = []
    for i, v in enumerate(values):
        k = f"{key_prefix}_{i}"
        parts.append(f"{field} LIKE ${k}")
        params[k] = v + "%"
    return "(" + " OR ".join(parts) + ")"


def _build_where(icp: ICP) -> tuple[list[str], dict[str, object]]:
    """Compose the WHERE-clause fragments and the parameter dict.

    Each fragment is independently appendable; only fields the user set
    on the ICP contribute. Headcount uses tranche overlap rather than
    strict containment: a tranche row qualifies if its [min, max] window
    intersects the ICP's [min, max] window (with NULLs treated as the
    open ends of the half-line).
    """
    clauses: list[str] = []
    params: dict[str, object] = {}

    if icp.naf_codes:
        normalized = [_normalize_naf_code(c) for c in icp.naf_codes]
        clauses.append(_prefix_or_clause("naf_code", normalized, params, "naf_p"))

    # Tranche overlap: tranche.max >= icp.min AND tranche.min <= icp.max.
    # NULL tranche_max means "10000+" (open right edge), so the >=
    # comparison passes by treating NULL as +inf via COALESCE.
    if icp.headcount_min is not None:
        clauses.append("COALESCE(tranche_max, 2147483647) >= $headcount_min")
        params["headcount_min"] = icp.headcount_min
    if icp.headcount_max is not None:
        clauses.append("COALESCE(tranche_min, 0) <= $headcount_max")
        params["headcount_max"] = icp.headcount_max

    if icp.region_code is not None:
        clauses.append("region_code = $region_code")
        params["region_code"] = icp.region_code

    if icp.department_codes:
        clauses.append("department_code IN (SELECT UNNEST($department_codes))")
        params["department_codes"] = list(icp.department_codes)

    if icp.postal_codes:
        clauses.append(_prefix_or_clause("postal_code", list(icp.postal_codes), params, "postal_p"))

    # Age filters compare against ``current_date`` rather than a fixed
    # epoch so re-runs against the same data are still date-relative.
    if icp.age_max_months is not None:
        clauses.append("creation_date >= (current_date - INTERVAL ($age_max_months) MONTH)")
        params["age_max_months"] = icp.age_max_months
    if icp.age_min_months is not None:
        clauses.append("creation_date <= (current_date - INTERVAL ($age_min_months) MONTH)")
        params["age_min_months"] = icp.age_min_months

    if icp.require_active:
        clauses.append("is_active = TRUE")

    return clauses, params


def search(icp: ICP, *, store: SireneStore, max_results: int = 1000) -> list[Company]:
    """Return up to ``max_results`` candidates matching ``icp``.

    Sorted by ``creation_date DESC`` so newer companies surface first.
    If the unbounded result count exceeds ``max_results``, the list is
    truncated and a warning is logged.
    """
    con = store.connect()
    clauses, params = _build_where(icp)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""

    # Fetch one extra row so we can detect (and log) silent truncation
    # without a separate COUNT(*) round-trip.
    params["limit_plus_one"] = max_results + 1
    sql = f"{_SELECT} {where} ORDER BY creation_date DESC NULLS LAST LIMIT $limit_plus_one"
    rows = con.execute(sql, params).fetchall()

    truncated = len(rows) > max_results
    if truncated:
        rows = rows[:max_results]
        log.warning(
            "search: result truncated at max_results=%d; broaden filters or raise the cap",
            max_results,
        )

    return [
        Company(
            siren=r[0],
            siret_main=r[1],
            name=r[2],
            naf_code=r[3],
            headcount_tranche=r[4],
            headcount_label=r[5],
            region_code=r[6],
            department_code=r[7],
            postal_code=r[8],
            commune=r[9],
            creation_date=r[10],
            is_active=r[11],
        )
        for r in rows
    ]
