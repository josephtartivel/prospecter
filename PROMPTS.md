# Build prompts

Scoped prompts for finishing the project, one Claude Code session per
section. Each one is self-contained — the agent reads the SPEC and the
relevant existing files first, then implements one slice, then stops.

The pattern that keeps Claude Code from drifting:

> *Read SPEC.md and these N files. Implement exactly the deliverables
> below. Don't refactor adjacent code unless it's broken. When done, list
> what you changed and what's still TODO.*

## Session 1 — DuckDB store + Search agent

> Read `SPEC.md` (especially §5, §7), `data/README.md`, `src/prospecter/schemas.py`,
> and the skeletons in `src/prospecter/agents/search.py` and
> `src/prospecter/tools/duckdb_tool.py`.
>
> Implement `SireneStore` end-to-end:
> - On `connect()`, create two DuckDB views over the SIRENE CSVs (or
>   Parquet if it exists), projecting only the columns referenced in
>   `Company`. Be explicit about column types and casting (`creation_date`
>   from `dateCreationUniteLegale`, `headcount_tranche` from
>   `trancheEffectifsUniteLegale`, etc.).
> - Implement a `materialize()` classmethod that writes the joined view
>   to `data/sirene/sirene.parquet` for repeat-run speedup. Idempotent.
>
> Then implement `search.search(icp, store=store, max_results=1000)`:
> - Build the WHERE clause from non-empty ICP fields. Parameterise with
>   `$param` style — never f-string SQL.
> - Map the SIRENE tranche codes to the (min, max) headcount pair so the
>   filter expresses *"the tranche overlaps the ICP range"*, not strict
>   inclusion.
> - Return a list of `Company` rows sorted by `creation_date DESC`,
>   capped at `max_results`. Log a warning if truncated.
>
> Add unit tests in `tests/test_search.py` against a tiny in-memory
> DuckDB fixture with ~30 hand-crafted rows. Don't mock — DuckDB is fast
> enough to run real queries in tests.
>
> When done: list every column you projected from each CSV, and note any
> SIRENE quirk you ran into (encoding issues, multi-row SIRENs, etc.).

## Session 2 — Scorer agent

> Read `SPEC.md` (§7 Scorer), `prompts/scorer_v1.md`,
> `src/prospecter/agents/icp_parser.py` (this is the reference pattern),
> and the skeleton in `src/prospecter/agents/scorer.py`.
>
> Implement `score_candidates(icp, candidates, llm, ...)` mirroring the
> parser pattern:
> - Single tool definition derived from `Score`'s JSON schema.
> - One LiteLLM call per candidate. Build messages with `(icp, company)`
>   serialised compactly into the user message; the system prompt is the
>   rubric.
> - Pass `cache_system_prompt=True` on every call.
> - Run candidates concurrently with `asyncio.gather` and a `Semaphore`
>   capped at `concurrency`. Return scores in the input order.
> - On Pydantic validation failure, retry once with the error appended;
>   on second failure, drop the candidate and append a "scoring failed"
>   trace event.
>
> Add a unit test in `tests/test_scorer.py` that uses a fake `LLM` (a
> hand-rolled stub responding with a fixed tool call) to verify
> ordering, retry-on-validation-error, and the drop-on-second-failure
> behaviour. Do not call a real model in unit tests.
>
> When done: report tokens-in / tokens-out per scorer call you observed
> in a quick smoke run.

## Session 3 — Wire the LangGraph nodes

> Read `SPEC.md` (§5), `src/prospecter/graph.py`, `src/prospecter/pipeline.py`,
> and the now-implemented `search` and `score_candidates` from sessions
> 1–2.
>
> Implement `search_node` and `score_node` in `graph.py`:
> - `search_node` calls `search(state.icp, store=...)`, sets
>   `state.candidates`, appends a `TraceEvent(kind="finish", ...)` with
>   counts.
> - `score_node` calls `asyncio.run(score_candidates(...))`, sets
>   `state.scores` (sorted by value desc), appends a finish event.
> - Both nodes wrap the agent call in try/except; on exception, set
>   `state.error` and route to `END` instead of crashing the graph.
>
> Verify `pipeline.run("Paris SaaS startups, 10–49 employees")` produces
> a CSV. Don't ship the real SIRENE dump in tests — add a `DataAvailable`
> pytest skip that activates only when `data/sirene/` is populated, then
> add one end-to-end smoke test there.
>
> When done: paste the first 5 rows of the CSV into the session output.

## Session 4 — Streamlit demo

> Read `app/streamlit_app.py`, `src/prospecter/graph.py`. The graph
> exposes `app.stream(state, stream_mode="updates")` which yields
> `{node_name: state_diff}` dicts.
>
> Build the UI:
> - Text input + "Run" button + top-N slider (already scaffolded).
> - On submit, build the graph, stream events, render each one as a
>   chat-style message: "ICPParser → parsed in 1.2s — { icp summary }",
>   "Search → 312 candidates", "Scorer → 50 scored, p95 0.9s".
> - When done, render `pd.DataFrame(leads)` with a download button for
>   the CSV.
> - Show total cost in the corner; surface the model used.
>
> Keep it under 200 lines. Don't add CSS frameworks; Streamlit's defaults
> are fine. No login, no sessions, no auth.

## Session 5 — Eval runner

> Read `SPEC.md` (§8), `eval/README.md`, `eval/runner.py`,
> `eval/metrics.py`, `eval/icps.jsonl`, `eval/configs/*.yaml`.
>
> Implement `eval/runner.py` end-to-end:
> - Loads all ICPs and the configs passed via `--configs`.
> - For each (config × ICP):
>   - Sets the relevant env vars from the config.
>   - Runs `pipeline.run(icp["nl"])`.
>   - Loads labels with `load_labels(icp["id"])` and computes
>     `precision_at_k`, `ndcg_at_k`.
>   - Records `cost_usd`, `latency_ms`.
> - After all ICPs for a config: writes
>   `eval/reports/{date}_{config_name}.json` with per-ICP details and an
>   aggregate from `metrics.aggregate(...)`.
> - Updates `eval/reports/latest.json` symlink (or copies on Windows).
>
> Print a Rich table summarising the configurations side-by-side after
> all configs run.
>
> Add `tests/test_runner.py` covering label loading, JSONL parsing, and
> the report-writing path with stub data. Don't call a real model.

## Session 6 — Build the labeled eval set (12 hours of human work, not Claude's)

> This is the unsexy session. Don't ask Claude to label for you — that
> defeats the purpose of the eval.
>
> For each ICP in `eval/icps.jsonl` (start with the 5 starters):
> 1. Run filter-only on the SIRENE store; capture the top 50 candidates
>    by `creation_date DESC`.
> 2. For each candidate, assign label 0/1/2 per `eval/README.md` rubric.
>    Spend ~30 seconds per candidate on average; ~25 minutes per ICP.
> 3. Save labels to `eval/labels/{icp_id}.json`.
>
> Expand `icps.jsonl` to 30 entries (10 per difficulty tier). Re-label.
>
> When done, run `uv run python -m eval.runner --configs eval/configs/*.yaml`
> and paste the result table into the README's Results section.

## Session 7 — Polish the README and "Where it fails" section

> Read the latest eval report. Update the README:
> - Fill the Results table with real numbers.
> - Replace placeholder bullets in "Where it fails" with concrete
>   failure cases observed in the eval — cite the ICP id and the
>   specific mistake.
> - Add an "Ablations that mattered" subsection with the with/without
>   scorer numbers, with/without prompt caching numbers if measured.
>
> Capture two screenshots: one of the Streamlit run and one of the CLI
> output. Drop them in `docs/img/` and reference from README.
>
> Final sanity check: does someone reading the README in 60 seconds
> understand what this project is, why it works, and how to run it? If
> not, cut sentences until yes.

---

## Voice rules (apply to every session)

- No emoji in code, no decorative banners.
- Comments explain *why*; let the code show *what*.
- Type hints at function boundaries, not on every local variable.
- Tests cover deterministic logic; the eval covers the agents.
- Update `SPEC.md` if a contract changes. Update `DECISIONS.md` if a
  major choice changes (and add a new ADR in `notes/`).
- Commit messages: terse, lowercase, no AI-attribution trailers.
