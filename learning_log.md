# Learning log — Prospecter

Append-only journal of what Claude taught me during each session.
Newer entries on top. Use this to consolidate weekly and to feed
`concepts_covered.md` with the patterns that have stuck.

---

## 2026-05-10 — Session 7: Cache-savings experiment ruled out (wrong provider)

**What I implemented**: nothing of substance. Tried to measure prompt-cache
savings (cold vs warm Anthropic ephemeral cache) and quote a delta in the
README. Caught early that the premise didn't hold for the active provider.
Single artifact shipped: a one-line baseline cost note in README under
`## Eval` (commit `69899bc`): `$0.0022 / ICP` on Mistral, `icp-001`,
`top_n=50`.

**Patterns named today**:
- *Provider-specific prompt-cache mechanics* — Anthropic's
  `cache_control: {type: ephemeral}` syntax is Anthropic-only.
  LiteLLM forwards it for Anthropic-flavoured models and silently drops
  it for everything else. Mistral La Plateforme does prefix-cache
  server-side but doesn't expose a separate cache-read price in the
  response, so `_hidden_params["response_cost"]` shows the same number
  cold and warm — there is no delta to quote. OpenAI prompt caching
  is yet a third mechanism with its own discount visibility.
- *Read the docstring before running the experiment* — `llm.py:127-129`
  says verbatim "no-op on non-Anthropic providers". Ten seconds of
  reading would have killed the wrong experiment before any code
  changes.

**Subtleties surfaced**:
- `eval/runner.py` does not call `load_dotenv()` — only `cli.py` does.
  Running `uv run python -m eval.runner` from a fresh shell yields an
  `AuthenticationError` because `ANTHROPIC_API_KEY` (or whichever) is
  in `.env` and never reaches `os.environ`. The CLI path sidesteps it
  by loading dotenv at the entry. Worth a one-line fix later.
- The eval runner has no single-ICP flag. For one-off probes, prefer
  reusing `prospecter run "<nl>"` (which already prints
  `cost ≈ $X.XXXX` from `state.cost_cents`) over adding a `--limit`
  flag to the runner. Fewer moving parts; the CLI is the right tool
  for ad-hoc cost questions.
- The `cost_cents` field on `RunState` is the canonical aggregate;
  `state.cost_cents / 100` rounded to 4 decimals gives the README-grade
  number. No need to re-traverse `LLM.history`.

**Senior signal of the session**: stopped a wrong experiment before
spending API credit on it. The earlier turn proposed running the eval
twice on Sonnet to measure ~57% cache savings — convincing-sounding,
but the active provider is Mistral, where the saving doesn't exist
on the wire. Reverted three premature edits to `eval/runner.py`
(`--limit`, `load_dotenv`, the slicing) when scope shrank back to a
single CLI probe.

**Suggested follow-up questions for next session**:
- Should `cache_system_prompt=True` raise or log a warning when called
  with a non-Anthropic model, instead of silently no-op'ing? Trade-off:
  explicit vs ergonomic for the model-swap eval matrix where the same
  call site sees Anthropic, Mistral and OpenAI in turn.
- Mistral does prefix-cache server-side. Does it actually show up as a
  bill-side discount even when `_hidden_params["response_cost"]` ignores
  it? Worth checking the next La Plateforme invoice against the sum of
  per-call `cost_usd` — if they diverge, the eval cost numbers are
  pessimistic by some constant factor.

**Open scope notes**:
- `eval/runner.py` lacks `load_dotenv()` — left as-is today since the
  CLI path covered the immediate need, but anyone running the runner
  from a non-shell-exporting environment will hit the same
  `AuthenticationError` I did. Three-line fix when next touching the
  runner.
- `/tmp/__nonexistent_dryrun__/` was created during the false-start
  before auth failed; harmless.

---

## 2026-05-10 — Session 6: Eval runner + cost-path test gap

**What I implemented**:
1. `eval/runner.py` end-to-end (commit `755464a`): per-(config × ICP)
   loop, scoped env mutation via context manager, JSON report per
   config, `latest.json` symlink (with copy fallback), Rich summary
   table. Lazy import of `prospecter.pipeline` inside `_run_one_icp`
   to dodge a collection-time test interaction.
2. `feat(scorer)` (commit `43bf204`): `score_candidates` now reads
   `PROSPECTER_CONCURRENCY` env var when caller doesn't pass the
   `concurrency=` kwarg. Matches the eval-config knob without
   threading a value through `pipeline.run` → `build_graph`.
3. `test(llm)` (commit `ae46b6a`): three tests covering `LLM.call`'s
   cost extraction by stubbing `litellm_completion` at the module
   boundary, not pre-loading `CallRecord` objects. Covers the gap I
   correctly identified: `TestCostWiring` was a false sense of
   security since it bypassed the parsing code entirely.

**Patterns named today**:
- *Stubbing at the external boundary, not the internal wrapper* — the
  difference between `monkeypatch.setattr(LLM, "from_env", ...)` and
  `monkeypatch.setattr(llm_mod, "litellm_completion", ...)` is the
  difference between "we exercise our copy of the contract" and "we
  exercise our parsing of the contract". The first only catches
  consumer-side bugs; the second also catches "did LiteLLM rename a
  field". Pick the layer one below the code you want to verify.
- *Scoped environment mutation via context manager* — `_scoped_env`
  saves prior values, applies updates, yields, restores in
  `finally`. The restore-on-crash branch is what makes this honest;
  without it, a config that crashes mid-run leaves
  `PROSPECTER_MODEL_*` polluted for the next config in the matrix
  and for any pytest run sharing the process.
- *Lazy import as collection-time workaround* — `from prospecter
  import pipeline` at module top of `eval/runner.py` triggered
  `prospecter.pipeline` import during pytest collection of
  `test_runner.py`, which froze a stale `LLM` symbol that
  `tests/test_llm.py`'s `importlib.reload` later invalidated. Lazy
  import sidesteps the chain. The right long-term fix is in the
  test (`monkeypatch.setattr` instead of `reload`), not in
  production code; the lazy import is a workaround with a comment
  pointing at the real cause.
- *Diagnose before fixing* — the bug report ("cost ≈ $0.0000 despite
  50 scored") didn't reproduce after rebuilding the venv. A probe
  call against Mistral showed `_hidden_params["response_cost"]`
  populated correctly; a probe through the full pipeline showed
  `state.cost_cents` reflecting real spend; a real `prospecter run`
  showed `$0.0014`. The "fix" was therefore not a code change but a
  test-coverage strengthening to prevent the *real* gap (parsing
  layer untested) from biting later.

**Subtleties surfaced**:
- *iCloud Drive corrupts Python venvs* — symptoms seen today:
  duplicate `tiktoken_ext/openai_public 2.py` (broke `import
  litellm` with "Duplicate encoding name gpt2"), duplicate `tests/
  test_runner 2.py` collected by pytest, `.git/refs/remotes/origin/
  main 2.lock` floating around, editable install losing its module
  reference periodically. The " 2" suffix is iCloud's conflict-
  resolution scheme. Fix: exclude `~/Documents/Projects/` from
  iCloud Drive at the system level. Workaround: `rm -rf .venv && uv
  sync --all-extras && uv pip install -e . --reinstall`. Took 30+
  minutes today before I pinpointed it; should be the *first*
  hypothesis next time `import prospecter` fails out of nowhere.
- *`asyncio.run` inside a sync LangGraph node* — `score_node` wraps
  `asyncio.run(score_candidates(...))` because LangGraph 1.x sync
  mode forbids async nodes (Session 3 lesson). The `to_thread` for
  `llm.call` inside `score_candidates` schedules sync work on the
  default thread pool *of that one event loop*. List append on
  `llm.history` is GIL-safe across those threads, so cost
  accumulation is reliable — but easy to second-guess.
- *Stale `.pyc` after editable-install corruption* — when iCloud
  damages the editable install, Python may still execute cached
  bytecode for *part* of the package while reporting `ModuleNotFound`
  for *other* parts. That can produce the appearance of a real
  behaviour bug (cost = 0) when the actual pipeline.py wire is
  correct in source. Always rebuild the venv before believing a
  reproducible-looking bug after any kind of environment damage.

**Senior signal of the session** — pushed back on a "fix the cost
bug" framing when the bug didn't reproduce, instead of busy-fixing
something. The reported $0.0000 was a stale-`.pyc` artefact; the
real gap was the test that *would* have caught it if it had
existed. Strengthening the test was the honest output, not a code
patch that didn't address anything reproducible.

**Open questions for next session**:
- The top-of-module workaround comment in `eval/runner.py` should
  go away once `tests/test_llm.py` stops calling `importlib.reload`.
  Is `monkeypatch.setattr(litellm, "success_callback", [])` enough
  to test the opt-in semantics, or does the module-import-time
  guard need a different angle?
- `int(os.environ.get("PROSPECTER_CONCURRENCY", "8"))` raises on
  non-numeric input. Should the runner validate at config-load
  time, or should the scorer fall back to 8 with `log.warning`?
  Validation at the boundary feels right; needs a unit test on
  `_config_env_updates` if so.

**Open scope notes**:
- `eval/runner.py:130-131` has redundant `if predicted else 0.0`
  guards around `precision_at_k`/`ndcg_at_k` — those functions
  already return 0.0 on empty input. Dead code, drop next pass.
- `report["config"] = cfg` dumps the entire YAML wholesale. Future
  YAML fields the runner doesn't consume will leak into reports.
  Filter or document.

---

## 2026-05-04 — Session 5: Search-side prefix expansion (NAF + postal)

**What I implemented** (three commits — one architectural surface):
1. Replaced `IN(...)` predicates on `naf_codes` and `postal_codes` in
   `agents/search.py` with prefix-tolerant `LIKE 'X%'` OR-lists,
   factored through a tiny `_prefix_or_clause` helper. Bumped
   `icp_parser` prompt to v2 to prefer prefixes over guessed
   sub-letters. ADR-008. Bench: LIKE 17ms vs IN 42ms median on parquet.
2. Empirical end-to-end run revealed Mistral Small emits NACE Rev2
   codes (`10.71Z`) deterministically — prompt v2 ignored. Added
   `_normalize_naf_code` to rewrite `XX.XXZ` to its 5-char prefix
   before the predicate runs. Boulangerie case: 0 → 1000 candidates
   (capped). ADR-009 supersedes part of ADR-008's "preserve failure
   signal" position.
3. The 1000-candidate result triggered a downstream cost cascade
   (1000 LLM scorer calls per ICP run, ~$0.15 × 30 eval ICPs ≈ $4.5
   and ~50 minutes wall-clock). Bounded it at the search side: SQL
   `ORDER BY` extended with `(has_name, tranche_fit, creation_date)`,
   `max_results` default 1000 → 50 to align with
   `eval/bootstrap_labels`. Search contract changes from "every match"
   to "top-N most actionable". 20× cost reduction. ADR-010. All 30
   search tests green; first wrong attempt was a separate prerank
   agent — corrected after pushback.

**Patterns named today**:
- *Brittle exact-match on LLM-typed output* — if the LLM produces a
  string that feeds an exact-match query, every imperfection of the
  LLM (sub-letter confusion, NACE-vs-NAF, hallucinated codes)
  collapses the result set to zero. The cure depends on the shape of
  the input space: hierarchical fixed-width codes (NAF, postal) →
  prefix-tolerant predicate at the search side. Small enumerable sets
  (region, department) → Pydantic enum + retry-loop. Fuzzy non-
  enumerable space → tool-use lookup. Different cures, same anti-
  pattern. Recognise it once, you'll see it in every LLM-fed SQL or
  filter call.
- *Backward-compatible predicate change via fixed-width self-match* —
  swapping `IN ('62.02A')` for `naf_code LIKE '62.02A%'` is byte-
  equivalent only because the data is exactly 6 chars wide. If the
  column were variable-width, `LIKE 'XXX%'` would bring in extra
  rows. Worth verifying the assumption (`SELECT DISTINCT length(...)`
  in production data) before claiming back-compat. The argument lives
  in ADR-008.
- *DuckDB compiles fixed-prefix LIKE to a fast scalar predicate* —
  measured 17ms vs 42ms vs `IN(SELECT UNNEST(...))`. The IN form
  materialises a list and runs a hash probe; the LIKE form is a
  bytewise prefix check on a column scan, which the parquet zone
  maps may even prune. Counter-intuitive: "more general" predicate
  (LIKE) is faster than "more specific" one (IN) here. The lesson is
  not "always use LIKE" — it's "measure on your data, the optimiser
  is not magic".
- *Scope discipline on multi-axis fixes* — same anti-pattern existed
  on four ICP fields (NAF, postal, region, department) but the cures
  diverge. Including all four in one PR mixes two implementations
  (predicate vs schema) and three commits' worth of work. Bundling
  NAF+postal because they share the cure (one helper, two call sites)
  was the right grain; region/department go to the next session.
  Applied the rule "one session = one logical change".

**Subtleties surfaced**:
- *Architecture decisions are not free of empirical reality* — ADR-008
  rejected the Z-normalization on principled grounds ("preserve the
  failure signal"). The next end-to-end run showed the parser ignores
  prompt v2 deterministically, making the failure signal unactionable.
  ADR-009 reverses the position with explicit empirical justification.
  This is the right ADR practice: when reality contradicts a previous
  decision, write a new ADR that names what changed; don't quietly
  edit the old one. The git log + ADR sequence becomes the project's
  reasoning trail.
- *The Z-normalization is symmetric and bounded* — `XX.XXZ` → `XX.XX`
  works for both legitimate monolithic codes (no other `XX.XX*`
  exists in NAF, so the prefix matches only the original) and
  NACE-erroneous codes (the prefix matches the real subdivision).
  The trade-off is a narrow class of "truly hallucinated `XX.XXZ`"
  that now gets absorbed; we accept that for end-to-end correctness.
- Prompt v2 doesn't bump the ICP schema; it changes what the parser
  is *encouraged* to produce. The schema accepts both forms; v1 also
  accepts prefixes (it just doesn't suggest them). So a v1 vs v2 A/B
  is purely about parser behaviour, not contract.

**Open questions for next session**:
- How would `region_code` / `department_codes` look as Pydantic
  `Literal[...]` types (or a `RegionCode` enum)? The valid sets are
  small and stable; should the rejection happen at parse time
  (Pydantic) or be tolerated and let the search return 0?
- The `commune` null in production parquet (caught during bench) —
  is that a data-cleaning issue in `duckdb_tool.py` or a Pydantic
  model relaxation in `Company`? Pre-existing, separate session.
- Eval impact of `max_results=50` on P@10. Prior is no harm (the
  prerank signals select for the same properties the scorer would
  have selected anyway), but the eval set is what tells us. Worth
  running before the next architectural change so we have a clean
  baseline.

**Senior pattern surfaced (architecture pushback)**:
The first prerank plan was a separate `agents/prerank.py` module
with a `prerank()` function called between the search and scorer
graph nodes. It was rejected by the user on the grounds that prerank
is not an agent — it's an `ORDER BY` + `LIMIT` in the same SQL that
already does the WHERE filtering. Adding a Python layer would create
a new abstraction without a new behaviour. The corrected plan kept
the change inside `search.py` and made it a contract change, not a
new layer. Lesson: when the data and the predicate already live
together, putting the ranking somewhere else is "we have hammer, so
this looks like a nail" thinking. The smaller the surface of a fix,
the more honest it usually is.

---

## 2026-05-04 — Session 4: Langfuse observability via LiteLLM callback

**What I implemented**: Langfuse registered as a LiteLLM success/failure
callback in `llm.py`. Opt-in via `LANGFUSE_ENABLED=true`; when off, the
wrapper is byte-identical to a vanilla LiteLLM call (proven by
`tests/test_llm.py`). Every `pipeline.run(...)` carries a single
`run_id` propagated as `metadata.trace_id`, so Langfuse groups all
model calls of one run under one trace with cost, latency, tokens,
and full prompt/response.

**Patterns named today**:
- *Callback registration as observability seam* — LiteLLM exposes a
  `callbacks` list. Langfuse registers there and gets invoked on
  every success/failure without wrapping our own `llm.call()`. Same
  shape as web-framework middleware or OpenTelemetry auto-
  instrumentation: the library exposes a seam, third parties hook
  into it. The single chokepoint of `llm.py` is the natural place to
  plug in any cross-cutting concern (tracing, redaction, rate
  limiting) — recognise the seam pattern, you'll see it everywhere.
- *Trace grouping via `metadata.trace_id = run_id`* — Langfuse's data
  model is trace → observations. Without metadata each call becomes
  its own trace; with a shared `run_id`, all 50+ calls of one
  pipeline run roll up under one parent. Same idea as OpenTelemetry
  trace context: a UUID propagated through the work it triggered,
  so the platform can stitch it back together. The whole
  observability story breaks without consistent propagation.
- *Opt-in via env, byte-identical when off* — `LANGFUSE_ENABLED=false`
  (or unset) means no callback registration, no Langfuse import paid,
  no behaviour change. Two distinct claims here: "disabled" and
  "byte-identical". The test asserts the second, not just the first
  — different claim, different test. Matters for CI (no keys in
  secrets) and offline runs (no dashboard pollution).
- *State on the instance instead of threaded through every call* —
  the handoff doc proposed `run_id`, `icp_id`, `agent_name` as kwargs
  on `llm.call()`. The real `LLM` is stateful per-run, so I set
  `run_id`/`icp_id` once in `LLM.from_env(state.run_id, ...)` and
  kept only `agent_name` per-call. Agents take 1-line diffs instead
  of a 3-kwarg surgery. When 80% of the metadata is constant for the
  lifetime of an object, put it on the object, not on every function.

**Subtleties surfaced**:
- Stateful `LLM` per run only works if a fresh instance is created
  each `pipeline.run(...)`. A module-level singleton would silently
  contaminate `run_id` across runs and fuse unrelated traces in
  Langfuse. The check: `LLM.from_env(...)` happens *inside* `run(...)`,
  not at import time.
- Langfuse free tier is 50k observations/month. One full eval is
  ~30 ICPs × 50 candidates × 4 configs ≈ 6k calls. Headroom for
  ~8 full evals per month before the meter starts mattering.
- "Disabled" and "byte-identical" are different claims. The first
  means we don't ship traces; the second means the wrapper behaves
  exactly like vanilla LiteLLM. The test must assert the second.

**Senior signal of the session**: didn't execute the handoff doc as
a script. Read the existing code, saw the API mismatch (sync vs
async in the doc, stateful vs stateless), rescoped the design. The
diff in agent files dropped from "touch every call site" to "+1 line
each". A handoff doc is a recipe, not a contract.

**Suggested follow-up questions for next session**:
- Is the Langfuse callback fire-and-forget, or does it block on
  network I/O on the hot path? Matters for the streaming UI in
  Session 7 — a blocking callback per token would tank perceived
  latency.
- How does Langfuse handle partial failures mid-trace? If one
  candidate's scoring crashes, do the parent trace and sibling
  spans still flush, or do we lose the run?

**Open scope notes**:
- `docs/langfuse_trace.png` is referenced in the README but not
  created yet. Needs a real run with `LANGFUSE_ENABLED=true`, a
  screenshot of a representative trace, commit the image.
- Cache-warm cost delta is still TBD — depends on real eval runs
  and is on the post-Session-5b plan.

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
