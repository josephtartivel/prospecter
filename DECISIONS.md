# Decisions

Short log of the architectural choices that matter, written ADR-lite. Each
entry has Context, Decision, Consequences, and (where it earned one) the
alternatives considered and rejected. Newer entries on top.

The detailed write-ups live in `notes/NNNN-*.md`. This file is the index.

---

## ADR-008 — Search predicates accept prefixes for hierarchical taxonomy fields
**Status:** Accepted · **Date:** 2026-05-04

The Search agent translates an LLM-typed `ICP` into SQL. The original
predicate for `naf_codes` and `postal_codes` was `IN (...)`, which exact-
matches the LLM output against the SIRENE column verbatim. That works
when the parser is right and explodes silently to zero candidates when
it isn't. The dominant failure mode in practice is the parser confusing
NACE Rev2 (`10.71Z`) with NAF (`10.71A/B/C/D`), or picking the wrong
sub-letter under temperature-zero Mistral instability — verified case:
`["10.71A", "10.71B"]` returns 685 boulangeries while the bulk of the
target population (71k) sits at `10.71C` and gets missed. Postal codes
exhibit the same brittleness when the user names a department area
(`"Côte d'Azur"`) but the LLM emits one specific arrondissement.

NAF and postal are *hierarchical* — a partial code (`10.71`, `75`) is a
valid generalisation. We rewrite both predicates as a prefix-tolerant
`LIKE 'X%'` OR-list. SIRENE NAF is exactly six characters and postal
five, so a complete value's `LIKE 'XXXXXX%'` self-matches and the
predicate is byte-equivalent to the `IN(...)` it replaces — backward-
compatible with every full-code call site. On the parquet store the
LIKE predicate is in fact faster than `IN (SELECT UNNEST(...))`
(17ms vs 42ms median on a 62.02A scan), since DuckDB compiles
fixed-prefix LIKE to a scalar bytewise check while the IN form
materialises a list and runs a hash probe.

The cure is per-axis: prefix expansion fits hierarchical, fixed-width
codes (NAF, postal). It does not generalise to `region_code` or
`department_codes`, which are small enumerable sets where the LLM's
failure mode is hallucinating an off-list value (`"PACA"`, `"23"` for
old codes). Those will get a Pydantic enum + retry-loop fix in a
later session — same anti-pattern, different cure. We considered a
two-tier fallback (exact, then prefix on zero results) and rejected
it: implicit double-scan, masks parser regressions, and removes the
clean failure signal that `["10.71Z"]` returns zero. We considered
giving the parser a `lookup_naf` tool, but that adds a round-trip per
parse and breaks the latency budget — the search-side fix absorbs the
same class of error for ten lines of SQL. The `icp_parser` prompt is
bumped to v2 to teach the parser that prefixes are accepted, so the
robustness compounds rather than living entirely on the search side.

---

## ADR-007 — Langfuse for LLM observability
**Status:** Accepted · **Date:** 2026-05-03

Every model call already flows through `llm.py`; that single chokepoint
is the natural place to attach observability. We register Langfuse as a
LiteLLM success/failure callback and forward `metadata` (trace id,
session id, agent name, ICP id, tags) from the wrapper. Calls from one
prospecter run share `RunState.run_id`, so the Langfuse UI groups them
under a single trace with cost, latency, tokens, and the full
prompt/response. `LANGFUSE_ENABLED=false` (or unset) skips registration
entirely, leaving the wrapper byte-identical to a vanilla LiteLLM call —
useful for offline runs and CI.

We considered a hand-rolled file logger; it works but offers no UI, no
diff between runs, and no group-by-trace. We considered Helicone, an
HTTP proxy that sits on the request path — adding a network hop and a
vendor dependency to every model call to gain only what a callback
already gives us. We considered Arize Phoenix self-hosted; it has
stronger eval features but is heavier to run and overkill for a
single-laptop project. The Langfuse free tier (50k observations/month)
covers ~30 ICPs × 50 candidates × 4 configs = 6k calls per full eval
with margin. The integration is fifteen lines and disappears when
disabled.

---

## ADR-006 — LangGraph for orchestration
**Status:** Accepted · **Date:** 2026-05-01

The agent topology is fixed (parse → search → score) but we still want
streamed events for the Streamlit trace, typed shared state, and
checkpointing for resumable eval runs. Hand-rolling that is possible but
becomes the project's busiest module. LangGraph gives us all three for the
cost of one `StateGraph` definition. We use the SDK directly inside each
node — LangGraph is for orchestration, not for prompting magic. See
`notes/0001-langgraph-stack.md`.

---

## ADR-005 — LiteLLM as the model surface
**Status:** Accepted · **Date:** 2026-05-01

Prospecter is provider-agnostic: the same agent code runs against
Anthropic, OpenAI, DeepSeek, Mistral, or vLLM-served local models. Locking
to one SDK would prevent the multi-provider ablation that's a core
credibility signal of the eval. LiteLLM is the de-facto unified client in
2026. We accept its small overhead in exchange for one-line model swaps and
unified tool-use semantics. Provider-specific features (Anthropic prompt
caching, OpenAI structured outputs) are passed through as kwargs when
targeting the relevant model.

Rejected: a hand-rolled abstraction over `anthropic` + `openai` SDKs.
LiteLLM has solved this; rolling our own is NIH.

---

## ADR-004 — Streamlit for the demo, not FastAPI + React
**Status:** Accepted · **Date:** 2026-05-01

The demo exists to let a recruiter or interviewer poke at the agents and see
the trace. A production-shaped FastAPI + React stack would communicate
"engineer who builds production things" but adds a frontend codebase that
isn't part of the project's signal (which is agent design + eval rigor).
Streamlit gives 80% of the demo value with one Python file.

---

## ADR-003 — Tool-use for structured output, not JSON mode
**Status:** Accepted · **Date:** 2026-05-01

`ICPParser` and `Scorer` both need typed outputs. The two options are
tool-use (give the model a `submit_*` tool with a schema) and JSON mode
(free-text response with a parse step).

We use tool-use. The model is more reliable when the schema is part of the
tool definition rather than embedded in the prompt; failure mode is
"function arguments don't match schema" which Pydantic catches cleanly.
JSON mode failures are stringly-typed: missing brackets, trailing commas,
extra fields silently accepted. Tool-use also gives us a free retry
protocol — append the validation error as the tool result and continue.

LiteLLM normalises tool-use across providers; the same agent code works
against Anthropic, OpenAI, and DeepSeek without branching. Models that
don't support tool-use cleanly fall back to JSON mode (documented in the
scorer agent).

---

## ADR-002 — Prompts as versioned `.md` files, not Python strings
**Status:** Accepted · **Date:** 2026-05-01

System prompts for the parser and scorer are 200+ tokens of carefully tuned
text. We keep each as `prompts/{name}_v{n}.md`, loaded by a tiny
`PromptLibrary`. Diffing prompt iterations in git is the unit of work for
prompt engineering. f-string interpolation lives in the loader, not in the
prompt file (`{NAF_CODES}` style placeholders). Trade: a small indirection
when reading the agent code; we judge the diff-ability worth it.

---

## ADR-001 — DuckDB over Postgres for the SIRENE store
**Status:** Accepted · **Date:** 2026-05-01

SIRENE bulk dump is ~2 GB of CSV. DuckDB reads CSV directly (or Parquet if
we materialize), runs columnar SQL on a laptop, no server, no migrations.
A reader can `git clone && uv sync && python -m prospecter` and have it
working within five minutes. Postgres would be a credibility *signal* for
production but isn't load-bearing for a single-user prospecting tool. If we
ever needed concurrent multi-user querying, we'd revisit; not before.

See `notes/0002-duckdb-vs-postgres.md`.
