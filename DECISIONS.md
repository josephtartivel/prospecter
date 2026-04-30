# Decisions

Short log of the architectural choices that matter, written ADR-lite. Each
entry has Context, Decision, Consequences, and (where it earned one) the
alternatives considered and rejected. Newer entries on top.

The detailed write-ups live in `notes/NNNN-*.md`. This file is the index.

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
