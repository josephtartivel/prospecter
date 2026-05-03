# Learning log — Prospecter

Append-only journal of what Claude taught me during each session.
Newer entries on top. Use this to consolidate weekly and to feed
`concepts_covered.md` with the patterns that have stuck.

---

## 2026-05-03 — Session 3: LangGraph wiring + fail-soft routing

**What I implemented**: `search_node` and `score_node` in `graph.py`. Both
wrap the agent call in try/except, write any failure into `state.error`,
and route to `END` via `add_conditional_edges` instead of crashing the
graph. End-to-end smoke through `pipeline.run(...)` confirmed.

**Patterns named today**:
- *Fail-soft graph node* — try/except around the agent call, write the
  failure into state, return state. Never re-raise inside a node; let
  the topology decide what happens next.
- *Routed termination via `add_conditional_edges`* — branch on
  `state.error` to dispatch to `END`. Control flow lives in the graph
  topology, not in node-level mutation.
- *Sync-graph constraint under LangGraph 1.x* — mixing sync and async
  nodes crashes through `.invoke()`. Pick one mode per graph. Ours is
  sync, so the async scorer gets an `asyncio.run(...)` boundary inside
  the node.
- *Empirical verification before code* — ran a small LangGraph dispatch
  test to confirm sync/async behaviour in 1.x before committing to a
  design, instead of guessing.

**Subtleties surfaced**:
- Bonus bug fix in `pipeline.py`: `app.invoke(...)` returns a `dict`,
  not a `RunState` instance. Downstream code was treating it as the
  Pydantic model. The mismatch had been masked because an earlier
  `NotImplementedError` was tripping the pipeline before execution
  reached the dict, so the bug never surfaced until the graph actually
  ran end-to-end.
- LangGraph routes via the predicate passed to `add_conditional_edges`,
  not via state mutation alone — easy trap to set `state.error` and
  assume the graph self-routes.

**Senior signal of the session**: ran the empirical dispatch test
instead of asking Claude to "just try it". The answer changed the
design (sync graph, not mixed).

**Suggested follow-up questions for next session**:
- Is there a clean way to run async nodes natively in a single graph?
  (LangGraph's `ainvoke` / `astream` — revisit when wiring streaming UI
  in Session 7.)

**Open scope notes**:
- The `pipeline.py` fix landed in the same commit as the graph wiring.
  Defensible (one logical change: "make the graph run end-to-end") but
  in a stricter regime it would have been a separate `fix(pipeline):`
  commit.

---

## 2026-05-03 — Session 2: Scorer agent (tool-use, parallel)

**What I implemented**: `score_candidates(icp, candidates, llm, ...)`
mirroring the ICP-parser pattern: tool-use with a single Pydantic-
derived tool schema, one LiteLLM call per candidate, retry-on-
validation-error, parallel execution bounded by an `asyncio.Semaphore`.
Unit tests with a FakeLLM stub cover ordering, retry, and drop-on-
second-failure.

**Patterns named today**:
- *Tool-use with a Pydantic-derived schema* — one `Score` model, derive
  the JSON schema from it, hand it to the LLM as the only tool. Single
  source of truth between runtime validation and the wire format.
- *Retry-on-validation-error* — on `ValidationError`, retry once with
  the error appended to the messages; on second failure drop the
  candidate and emit a trace event. Reinforcement of the Session 1
  parser pattern.
- *Bounded concurrency via `asyncio.Semaphore`* — a semaphore caps
  in-flight LLM calls while `asyncio.gather` runs all coroutines.
  Prevents flooding the provider's rate limit; result order stays
  aligned to the input via gather's positional return.
- *Prompt caching with `cache_system_prompt=True`* — flag on the
  wrapper that emits a `cache_control: ephemeral` marker on the system
  block. With a stable rubric and a 5-min TTL, re-runs against the
  same ICP land hot. Real delta to be measured after Session 5b.
- *FakeLLM stub for agent tests* — hand-rolled object with the same
  shape as the real `LLM`, returning a fixed tool call. Avoids hitting
  a real model in unit tests; the eval covers the probabilistic path.
- *Targeted `# type: ignore[arg-type]` with comment* — narrow ignore at
  the LiteLLM messages boundary, with the error code and a one-line
  *why*, instead of a blanket ignore. Runtime is correct; LiteLLM's
  parameter type is overstated.

**Subtleties surfaced**:
- `asyncio.gather` preserves input order regardless of completion
  order. With the semaphore bounding parallelism, scores still come
  back aligned to the candidate list — no manual reordering needed.
- Appending the raw `ValidationError` text on retry gives the model a
  concrete signal of what to fix, not a generic "try again".

**Senior signal of the session**: kept the FakeLLM stub minimal —
single fixed response, no recording layer — and resisted building a
"test framework" around it. Three lines of stub beats a clever
harness.

**Suggested follow-up questions for next session**:
- What happens to ordering if a coroutine raises inside `gather`?
  (`return_exceptions=True` vs default — worth a refresher before
  streaming UI work in Session 7.)

**Open scope notes**:
- The `# type: ignore[arg-type]` is a smell pointing at LiteLLM's
  narrow `messages` typing. If it shows up a third time, factor a tiny
  adapter and document.

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
