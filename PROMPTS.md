# Build prompts

Scoped prompts for finishing the project, one Claude Code session per
section. Each one is self-contained — the agent reads the SPEC and the
relevant existing files first, then implements one slice, then stops.

The pattern that keeps Claude Code from drifting:

> *Read SPEC.md and these N files. Implement exactly the deliverables
> below. Don't refactor adjacent code unless it's broken. When done, list
> what you changed and what's still TODO.*

A few of the sessions reference drop-in specs and code in `_handoff/`.
That folder is staging — each session reads the relevant handoff file,
adapts it to the real codebase, then `git rm`s it. When all sessions are
done, `_handoff/` is empty and gets removed.

## Session 1 — DuckDB store + Search agent

> Read `SPEC.md` (especially §5, §7), `data/README.md`, `src/prospecter/schemas.py`,
> and the skeletons in `src/prospecter/agents/search.py` and
> `src/prospecter/tools/duckdb_tool.py`.
>
> Implement `SireneStore` end-to-end:
>
> * On `connect()`, create two DuckDB views over the SIRENE CSVs (or
>   Parquet if it exists), projecting only the columns referenced in
>   `Company`. Be explicit about column types and casting (`creation_date`
>   from `dateCreationUniteLegale`, `headcount_tranche` from
>   `trancheEffectifsUniteLegale`, etc.).
> * Implement a `materialize()` classmethod that writes the joined view
>   to `data/sirene/sirene.parquet` for repeat-run speedup. Idempotent.
>
> Then implement `search.search(icp, store=store, max_results=1000)`:
>
> * Build the WHERE clause from non-empty ICP fields. Parameterise with
>   `$param` style — never f-string SQL.
> * Map the SIRENE tranche codes to the (min, max) headcount pair so the
>   filter expresses *"the tranche overlaps the ICP range"*, not strict
>   inclusion.
> * Return a list of `Company` rows sorted by `creation_date DESC`,
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
>
> * Single tool definition derived from `Score`'s JSON schema.
> * One LiteLLM call per candidate. Build messages with `(icp, company)`
>   serialised compactly into the user message; the system prompt is the
>   rubric.
> * Pass `cache_system_prompt=True` on every call.
> * Run candidates concurrently with `asyncio.gather` and a `Semaphore`
>   capped at `concurrency`. Return scores in the input order.
> * On Pydantic validation failure, retry once with the error appended;
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
>
> * `search_node` calls `search(state.icp, store=...)`, sets
>   `state.candidates`, appends a `TraceEvent(kind="finish", ...)` with
>   counts.
> * `score_node` calls `asyncio.run(score_candidates(...))`, sets
>   `state.scores` (sorted by value desc), appends a finish event.
> * Both nodes wrap the agent call in try/except; on exception, set
>   `state.error` and route to `END` instead of crashing the graph.
>
> Verify `pipeline.run("Paris SaaS startups, 10–49 employees")` produces
> a CSV. Don't ship the real SIRENE dump in tests — add a `DataAvailable`
> pytest skip that activates only when `data/sirene/` is populated, then
> add one end-to-end smoke test there.
>
> When done: paste the first 5 rows of the CSV into the session output.

## Session 4 — Langfuse observability

> Read `_handoff/LANGFUSE_INTEGRATION.md` first — it has the exact diff,
> env vars, and an ADR draft. Then read `src/prospecter/llm.py` and
> `src/prospecter/agents/icp_parser.py` to confirm the wrapper signature
> and how agents call it today.
>
> Integrate Langfuse via the LiteLLM callback hook so every LLM call
> shows up in the Langfuse UI with cost, latency, tokens, and trace
> grouping per ICP run:
>
> * Add `langfuse>=2.50.0` to `pyproject.toml`. Run `uv sync`.
> * Add the Langfuse env vars (public key, secret key, host, enabled flag)
>   to `.env.example`. No real keys in any committed file.
> * Patch `llm.py`: register the callback when `LANGFUSE_ENABLED=true`,
>   accept `run_id`, `agent_name`, `icp_id` kwargs, forward them as
>   `metadata` to `litellm.acompletion`. Adapt to the real wrapper
>   signature; do not refactor it beyond what's needed.
> * Update `agents/icp_parser.py` and `agents/scorer.py` (when it exists)
>   to pass `run_id` and `agent_name`. Add `icp_id` to `RunState` if
>   missing.
> * Add an "ADR-007 — Langfuse for LLM observability" entry to
>   `DECISIONS.md` (newest on top, matching existing tone). Use the
>   handoff draft as raw material but rewrite to match the prose style
>   of ADR-005 / ADR-006 (no bulleted alternatives — fold them into
>   prose). Skip the `notes/` write-up unless the rationale needs more
>   than 8 lines.
> * Add a one-line test in `tests/` that imports `llm` with
>   `LANGFUSE_ENABLED=false` and confirms no crash and no key required.
> * Update `README.md`: add a 4-line "Observability" section after
>   "Eval", reserving a spot for a screenshot at `docs/langfuse_trace.png`.
>   Don't take or commit the screenshot in this session — that's a manual
>   step you do once you've actually run something through Langfuse.
>
> Hard constraints: if `LANGFUSE_ENABLED` is false or unset, the wrapper
> must behave exactly as before. Test this. If you find yourself
> rewriting more than 30 lines, stop and ask.
>
> When done: print the diff stats, the test results, the new env vars,
> and the new ADR title. Then `git rm _handoff/LANGFUSE_INTEGRATION.md`
> and amend the commit.

## Session 5 — Eval runner

> Read `SPEC.md` (§8), `eval/README.md`, `eval/runner.py`,
> `eval/metrics.py`, `eval/icps.jsonl`, `eval/configs/*.yaml`.
>
> Implement `eval/runner.py` end-to-end:
>
> * Loads all ICPs and the configs passed via `--configs`.
> * For each (config × ICP):
>   + Sets the relevant env vars from the config.
>   + Runs `pipeline.run(icp["nl"])`.
>   + Loads labels with `load_labels(icp["id"])` and computes
>     `precision_at_k`, `ndcg_at_k`.
>   + Records `cost_usd`, `latency_ms`.
> * After all ICPs for a config: writes
>   `eval/reports/{date}_{config_name}.json` with per-ICP details and an
>   aggregate from `metrics.aggregate(...)`.
> * Updates `eval/reports/latest.json` symlink (or copies on Windows).
>
> Print a Rich table summarising the configurations side-by-side after
> all configs run.
>
> Add `tests/test_runner.py` covering label loading, JSONL parsing, and
> the report-writing path with stub data. Don't call a real model.

## Session 5b — Polish additions (vLLM config, max-cost flag, DSPy ADR)

> Read `SPEC.md`, `DECISIONS.md`, `notes/0002-prompts-as-versioned-md.md`,
> and `src/prospecter/cli.py`, `src/prospecter/pipeline.py`,
> `src/prospecter/llm.py`, `eval/configs/haiku.yaml`,
> `eval/configs/sonnet.yaml`.
>
> This session adds three small polish items. Strict scope. No
> refactoring of adjacent code. Two commits.
>
> **Commit 1 — vLLM config + max-cost flag**
>
> Create `eval/configs/vllm-local.yaml` mirroring the existing configs,
> pointing to a vLLM-served OpenAI-compatible endpoint:
>
> * `name: vllm-local`
> * `model: openai/Qwen/Qwen2.5-7B-Instruct`
> * `api_base: http://localhost:8000/v1`
> * `api_key: dummy` (vLLM ignores; litellm requires the field)
> * `temperature: 0.0`, `max_tokens: 256`
> * Description noting that this proves the pipeline is provider-
>   agnostic — swap a YAML, not code.
>
> Do NOT actually run an eval against this config in this session.
>
> Add `--max-cost-usd FLOAT` to the `run` typer command in `cli.py`
> (default None). In the pipeline (or `llm.py` — pick the layer that
> aggregates cost), check after each LLM call: if running total exceeds
> the limit, raise `BudgetExceeded(spend_usd=...)`. Catch in the CLI,
> exit non-zero with a clean error line.
>
> Add a unit test that asserts `BudgetExceeded` triggers when running
> total crosses the threshold (use a stub LLM with fixed cost per call).
>
> Update README "Run" section example:
>
>     uv run prospecter run "..." --max-cost-usd 0.05
>
> Run ruff, pyright, pytest. Commit:
> "feat(cli): --max-cost-usd budget guard + vllm-local eval config".
>
> **Commit 2 — ADR-002 update (DSPy alternative)**
>
> Open `DECISIONS.md`. Find the "ADR-002 — Prompts as versioned `.md`
> files, not Python strings" section. Append a final paragraph in the
> existing tone (no list bullets — match the rest of the file):
>
>     We considered DSPy, which compiles prompts from input/output
>     examples and a metric. It is excellent for iterating on a fixed
>     task once you have labels, but it adds a magic layer between
>     prompt source and the bytes the model sees — defeating the
>     "diff prompt v1 vs v2 in git" workflow that is the whole point
>     of this ADR. The prompts here are short and stable enough that
>     hand-iteration is faster than maintaining a DSPy program.
>
> No code change. Commit: "docs(adr-002): note dspy as considered
> alternative for prompts".
>
> Hard constraints: do NOT modify any agent file unless the budget
> check requires it. Do NOT add a new dependency (vLLM works through
> litellm's openai-compatible adapter). Do NOT actually run an eval in
> this session. If the budget check would require restructuring how
> cost is tracked, stop and ask — the simple version (running total +
> check after each call) should be a 10-line addition.
>
> When done: print the new yaml content, the diff stats for cli.py and
> pipeline.py, the new ADR section as rendered markdown, and test
> results.

## Manual measurement after Session 5b — prompt caching delta

You already have `cache_system_prompt=True` per ADR-003 / Session 2.
For a real measured number in the README, run the eval twice on the
same ICP and observe the cost delta:

```
uv run python -m eval.runner --configs eval/configs/sonnet.yaml \
    --icps eval/icps.jsonl --first-only
# note cost_usd from eval/reports/latest.json
# wait < 5 min so the Anthropic cache is warm
uv run python -m eval.runner --configs eval/configs/sonnet.yaml \
    --icps eval/icps.jsonl --first-only
# note new cost_usd, compute delta
```

Add one line to README "Eval", e.g.:

> Prompt caching on the scorer rubric (50 candidates / ICP):
> **$0.0042 → $0.0018 per ICP, -57%** on cache-warm runs.

Real number. Worth more than the caching ADR by itself.

## Session 6 — Bootstrap eval labels (was: 12 hours of human work)

> Read `_handoff/LABELING_PROTOCOL.md` first for the methodology. Three
> drop-in scripts live in `_handoff/`: `bootstrap_labels.py`,
> `review_labels.py`, `kappa.py`. Then read `eval/icps.jsonl`,
> `eval/metrics.py`, and `eval/runner.py` to learn the existing shapes.
>
> This session ships the *tools* for labeling, not the labels themselves.
> The actual labeling is manual work the user does after this session
> ends.
>
> First, add a missing piece: `eval/gather_candidates.py`. This script
> runs the Search agent ONLY (no scorer) on each ICP and writes
> `eval/candidates/{icp_id}.json` with the top-50 candidates per ICP.
> Reuse the pipeline plumbing — do not duplicate DuckDB query code.
>
> Then place the three handoff scripts at:
>
> * `eval/bootstrap_labels.py` — adapt the `from prospecter import llm`
>   import and the LabelTask field names to match what
>   `gather_candidates.py` actually emits.
> * `eval/review_labels.py` — pure stdlib + typer + rich, drop in.
> * `eval/kappa.py` — pure stdlib, drop in.
>
> Update `eval/README.md` to a 30-line summary of the protocol: schema,
> bootstrap step, review step, kappa step, the README sentence to copy
> once kappa is computed.
>
> Add unit tests in `tests/test_eval_labeling.py`:
>
> * `cohens_kappa([0,1,2,0,1], [0,1,2,0,1]) == 1.0`
> * `cohens_kappa([0,1,2,0,1], [2,1,0,2,1])` near zero or negative
> * `_parse_label("2") == 2`, `_parse_label("Label: 1") == 1`,
>   `_parse_label("nope") is None`
> * `review_labels`: load a fake bootstrap JSON, simulate one keep + one
>   override, assert resulting human-labels JSON is correct.
>
> Hard constraints: do NOT actually run the labeling pipeline in this
> session — that's the user's job once SIRENE data is downloaded. Do NOT
> bootstrap labels in CI. Do NOT add scikit-learn or numpy as a
> dependency just for kappa.
>
> When done: list new files under `eval/`, paste test results, then
> `git rm` the three handoff files and amend the commit.

## Manual interlude — run the eval (no Claude Code session)

This is on you, in a terminal, no Claude. The order:

```
./scripts/fetch_sirene.sh                                 # if not already
uv run python -m eval.gather_candidates --icps eval/icps.jsonl --out eval/candidates/
uv run python -m eval.bootstrap_labels --candidates eval/candidates/ \
    --icps eval/icps.jsonl --out eval/labels_bootstrap/   # ~$9, 30 min
uv run python -m eval.review_labels --all                 # ~3-4h focused
uv run python -m eval.kappa --bootstrap eval/labels_bootstrap/ --human eval/labels/
uv run python -m eval.runner --configs eval/configs/*.yaml
```

After this, `eval/reports/latest.json` has real numbers and the kappa
output prints a sentence to copy into the README.

## Session 7 — Streamlit demo

> Read `app/streamlit_app.py`, `src/prospecter/graph.py`. The graph
> exposes `app.stream(state, stream_mode="updates")` which yields
> `{node_name: state_diff}` dicts.
>
> Build the UI:
>
> * Text input + "Run" button + top-N slider (already scaffolded).
> * On submit, build the graph, stream events, render each one as a
>   chat-style message: "ICPParser → parsed in 1.2s — { icp summary }",
>   "Search → 312 candidates", "Scorer → 50 scored, p95 0.9s".
> * When done, render `pd.DataFrame(leads)` with a download button for
>   the CSV.
> * Show total cost in the corner; surface the model used.
>
> Keep it under 200 lines. Don't add CSS frameworks; Streamlit's defaults
> are fine. No login, no sessions, no auth.

## Session 8 — MCP server interface

> Read `_handoff/mcp_server.py` first — it is a complete drop-in file,
> but you must adapt it to the real signatures of `pipeline.run()` and
> the `Lead` schema. Then read `src/prospecter/pipeline.py` and
> `src/prospecter/schemas.py` to learn the actual signatures.
>
> Expose the prospecter pipeline as an MCP server so any MCP-compatible
> client (Claude Desktop, Cursor, Cline) can call it as a tool:
>
> * Add `mcp[cli]>=1.0.0` to `pyproject.toml`. Run `uv sync`.
> * Place the file at `src/prospecter/mcp_server.py`, adapting:
>   the import of `run_pipeline` and `Lead`, the
>   `_format_leads_as_markdown` function to match real fields, any
>   signature mismatches. Do NOT change the tool name, the docstring,
>   or the resource URI.
> * Add to `pyproject.toml`:
>   `[project.scripts]` `prospecter-mcp = "prospecter.mcp_server:main"`.
> * Add an "ADR-008 — MCP server interface" entry to `DECISIONS.md`
>   (newest on top, matching existing tone). Cover: why MCP, alternatives
>   rejected (REST API, gRPC, plain CLI invocation), consequences. Match
>   the prose style of ADR-005 / ADR-006 (no bulleted alternatives —
>   fold them into prose). Skip the `notes/` write-up unless rationale
>   needs more than 8 lines.
> * Update `README.md`: add a "Use from Claude Desktop" section after
>   "Run" with the exact JSON config block from the docstring, wrapped
>   to the README's line width.
> * Add a smoke test `tests/test_mcp_server.py`: import the module,
>   assert `mcp.tools` contains "prospect", assert the tool schema
>   validates against expected Pydantic types. Do NOT spin up a stdio
>   server in tests.
>
> Hard constraints: the MCP server must NOT add state — stateless across
> calls. Tool errors must NOT crash the server (catch and return as text).
> Do NOT add authentication — out of scope per SPEC non-goals. If you
> find yourself adding more than one tool or one resource, stop and ask.
>
> When done: print the tool schema, the new pyproject script, test
> results, and the README diff. Then `git rm _handoff/mcp_server.py` and
> amend the commit.

## Session 9 — Polish the README and "Where it fails" section

> Read the latest report in `eval/reports/`, plus `README.md`, `SPEC.md`,
> `DECISIONS.md`, `eval/README.md`. Look at the actual numbers achieved.
>
> Reorder `README.md` into this exact structure:
>
> 1. Title + one-sentence pitch
> 2. 90-second Loom (placeholder URL: `[demo: <Loom URL>]`)
> 3. Architecture diagram (Mermaid, embedded — no external image)
> 4. Setup
> 5. Run (CLI + Streamlit + MCP)
> 6. Pipeline (the existing 3-paragraph description)
> 7. Eval (with the FILLED Results table, plus the kappa sentence)
> 8. Observability (Langfuse, with the screenshot link)
> 9. Where it fails (NEW — see below)
> 10. Notes / non-goals
> 11. License
>
> Replace every "tbd" in the Results table with a real number. Round
> costs to 4 decimals, latencies to integers. Add a final column
> "vs Sonnet" computed as "P@10 delta in pts / cost ratio". This is the
> signature row of the project.
>
> Write a "Where it fails" section. 3 to 5 cases, each in this format:
>
> ```
> ### <one-line case>
> <2-3 sentences: what the input was, what we returned, why it was
> wrong, what the fix would look like in Phase 2>
> ```
>
> Pull these from real eval runs — look at the lowest-scoring ICPs in
> the report and pick the most instructive failures.
>
> Add a Mermaid diagram of the LangGraph pipeline (parse → search →
> score → END), with the data types on the edges (NL string → ICP →
> list[Company] → list[Lead]).
>
> Final sanity check: does someone reading the README in 60 seconds
> understand what this project is, why it works, and how to run it? If
> not, cut sentences until yes.
>
> Hard constraints: do NOT add new features. README only. Do NOT lie
> about results — leave a cell empty rather than guess. Do NOT remove
> the existing "Notes" section about SIRENE legal data and headcount
> tranches. Do NOT add badges (CI badge OK, no fake codecov, no LOC,
> no "made with love").

---

## Voice rules (apply to every session)

* No emoji in code, no decorative banners.
* Comments explain *why*; let the code show *what*.
* Type hints at function boundaries, not on every local variable.
* Tests cover deterministic logic; the eval covers the agents.
* Update `SPEC.md` if a contract changes. Update `DECISIONS.md` if a
  major choice changes (and add a new ADR in `notes/`).
* Commit messages: terse, lowercase, no AI-attribution trailers.
* One session = one terminal = one commit (max 2-3). If a session is
  growing past that, you've slipped scope. Stop and split.
