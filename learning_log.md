# Learning log — Prospecter

Append-only journal of what Claude taught me during each session.
Newer entries on top. Use this to consolidate weekly and to feed
`concepts_covered.md` with the patterns that have stuck.

---

## 2026-05-02 — Session 1: DuckDB SIRENE store + Search agent

**What I implemented**: `SireneStore` (DuckDB connection + companies
view + materialize() to parquet) and `search()` (deterministic ICP →
list[Company] via parameterised query). 19 new tests.

**Patterns named today**:
- *Tranche-overlap predicate* — filter on bucketed ranges via
  `bucket.max >= icp.min AND bucket.min <= icp.max`, with `COALESCE`
  for open-ended buckets. → added to concepts_covered.
- *TRY_CAST + NULLS LAST* — robust date casting plus ordering that
  keeps bad data off the top. → added to concepts_covered.
- *Parameterised SQL with `$name`* — never f-string SQL.
  → added to concepts_covered.

**Subtleties surfaced**:
- SIRENE has multi-row SIRENs (one unité légale, N établissements).
  The `companies` view collapses to siège-only via
  `CAST(etablissementSiege AS VARCHAR) IN ('true', 'TRUE', '1')`.
  Without this, the same SIREN appears N times under different
  postal codes.
- DOM postal codes (971xx-976xx) need 3-digit department extraction,
  not 2 — handled in a `CASE WHEN substr(...,1,2)='97' THEN substr(...,1,3)`.
- Open-ended top tranche "53" ships as `(10000, NULL, "10000+")`.
  Filter side uses `COALESCE(tranche_max, INT_MAX)` to make it always
  match the `>=` test.

**Senior signal of the session**: pushed back on Claude's plan when it
proposed an env var (`PROSPECTER_INCLUDE_RESTRICTED`) and a `force=True`
flag — both YAGNI / scope-creep. Removed before code was written.

**Suggested follow-up questions for next session**:
- Where else in the codebase will the tranche-overlap pattern apply?
  (Probably nowhere — it's specific to SIRENE buckets, but worth being
  alert when modelling other bucketed sources.)
- The materialize() function isn't tested end-to-end — should it have
  an integration test that actually writes a parquet? (Probably not
  in this repo since data/ is 16 GB; document the limitation.)

**Open scope notes**:
- `uv.lock` was created mid-session by `uv sync`. Should have been
  committed at scaffolding time. Going forward: run `uv sync` and
  commit lockfile during repo init.
- Pre-existing format diffs in `cli.py`, `llm.py`, `pipeline.py`,
  `schemas.py` — left untouched per "don't refactor adjacent code"
  rule. Worth a separate `chore: ruff format pass` commit at some
  point before the next feature work that touches them.
