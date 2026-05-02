# 0002 — DuckDB over Postgres for the SIRENE store

**Date:** 2026-04-30 · **Status:** Accepted

## Context

SIRENE bulk dump is two CSV files, ~2 GB compressed and ~16 GB unzipped,
at 30M (legal entities) and 40M (establishments) rows. We need to filter
on a handful of columns (NAF code, headcount tranche, region/department,
creation date) and return up to 1000 rows per ICP.

Workload shape:
- Single user, single machine.
- Scans dominate (we're filtering on indexed-ish columns but the data
  doesn't change between runs).
- Cold-start time matters — we want `git clone && uv sync && python -m
  prospecter` to work in five minutes, not "set up a Postgres cluster".

## Considered

1. **DuckDB.** Embedded columnar engine that reads CSV (or Parquet)
   directly, no server, no migrations. Scans 30M rows in seconds with
   column projection. Standard SQL. `pip install duckdb`.
2. **Postgres + COPY.** Production-shaped, well understood, GIN indexes
   on text columns would be faster for fuzzy queries we don't have.
   Costs: install, configure, run a server, write migrations, manage
   credentials, restore on every laptop swap.
3. **SQLite.** Embedded like DuckDB, but row-store; full-table scans
   over 30M rows are slow without indexes; building indexes adds another
   step.
4. **In-memory pandas.** Fits in RAM if we project columns, but every
   query rebuilds filter masks; SQL is cleaner and DuckDB is faster.

## Decision

**DuckDB**, reading from CSVs at first, with a one-shot Parquet
materialisation as an opt-in for repeat-run speedup.

## Consequences

- Setup is `./scripts/fetch_sirene.sh && uv run python -m prospecter ...`.
  No server.
- Queries are standard SQL — anyone reading the project knows the
  language already.
- We lose Postgres-shaped production credibility. We accept that trade
  because (a) this isn't a production multi-tenant system, (b) DuckDB is
  itself a 2026-credible choice for analytical workloads, and (c) we
  document the choice explicitly so it doesn't read as ignorance.
- If we ever need concurrent multi-user querying (we don't), the migration
  to Postgres is straightforward — schema is plain SQL, queries are
  parameterised.
