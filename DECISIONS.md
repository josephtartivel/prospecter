# Decisions

Short log of the architectural choices that matter, written ADR-lite. Each
entry has Context, Decision, Consequences, and (where it earned one) the
alternatives considered and rejected. Newer entries on top.

The detailed write-ups live in `notes/NNNN-*.md`. This file is the index.

---

## ADR-010 — Search agent contract: top-N deterministic ordering
**Status:** Accepted · **Date:** 2026-05-04

ADR-008 and ADR-009 made the search robust to LLM taxonomy errors and
recovered the boulangerie case from 0 to 1000 candidates. The
downstream effect was a cost cascade: the scorer agent runs one LLM
call per candidate, so 1000 candidates per ICP run × 30 ICPs in the
eval set = 30k Mistral Small calls per full eval, ~$3-5 and ~30
minutes wall-clock. The cascade is the natural consequence of
combining a permissive search with a per-row LLM scorer; either side
must bound it.

We bound it at the search side. The search contract changes from
*"every row matching the hard filter"* to *"top N most actionable
candidates by deterministic signal"*. The ranking is a SQL `ORDER BY`
chain — no new module, no new graph node, no new abstractions. Three
signals, evaluated lexicographically:

1. `has_name`: rows with empty `denominationUniteLegale` are demoted
   to the bottom. SIRENE has a long tail of shell entities with no
   public denomination; these are not actionable leads regardless of
   how well they match the filter, so they belong below any named
   row even when newer.
2. `tranche_fit`: candidates whose headcount tranche is *entirely
   within* the ICP `[min, max]` range rank above those that merely
   *overlap* an edge, which in turn rank above tranche `NN`
   (unknown bounds — kept under uncertainty per the existing ICP
   spec, but ranked last).
3. `creation_date DESC NULLS LAST`: stable tiebreak, newest first
   — same as the previous default ordering.

`max_results` default drops from 1000 to 50, matching the size of
`eval/bootstrap_labels`'s top-50/ICP labelling so prod and eval see
the same candidate distribution. The scorer becomes a re-ranker on
50 candidates rather than a bulk filter on 1000 — Mistral cost per
ICP drops by 20×; eval set wall-clock drops to ~2-3 minutes.

We considered a separate prerank agent and rejected it. The signals
are deterministic and column-bound, the ordering belongs in the same
SQL that already does the WHERE filtering — adding a Python layer
between search and score would be a new abstraction without a new
behaviour. We considered weighted scoring (sum of normalised signals
with tunable weights) and rejected it — lexicographic ordering has
no magic constants and is trivially testable. We considered keeping
the 1000 cap and pushing the scoring concern downstream (batch
scoring, smaller scoring model) — both are larger interventions
than a contract change at the source.

The trade is recall on the long tail of well-fitting candidates that
fall below position 50 in the deterministic ranking. The eval set
will measure whether this hurts P@10 in practice; the prior is no,
because the prerank signals select for the same properties the
scorer would have selected anyway.

---

## ADR-009 — Search absorbs the NACE-vs-NAF parser failure
**Status:** Accepted · **Date:** 2026-05-04 · **Supersedes part of ADR-008**

ADR-008 explicitly rejected absorbing parser failures, on the grounds
that `["10.71Z"]` returning zero is "the clean failure signal that we
preserve". Empirical run on the verified case (*"boulangeries
artisanales en Île-de-France de 5 à 20 employés"*) with Mistral Small
as the parser reverses that position: the parser emits `["10.71Z"]`
deterministically, and the v2 prompt instruction to prefer prefixes
over guessed sub-letters is ignored. The "clean failure signal" is
unactionable for this parser — it just becomes "0 candidates, why?"
and offers no path to recovery short of swapping the model. We have
no Anthropic key for this project, and OpenAI/DeepSeek would still
leave the failure mode latent for any future operator running on
Mistral.

We add a normalisation step in `_build_where`: a code matching the
regex `^\d{2}\.\d{2}Z$` is rewritten to its 5-char prefix before the
prefix-or predicate runs (`10.71Z` → `10.71`). The rule is precise
and motivated by the structure of NAF itself. In NAF, `XX.XXZ` is
*either* a legitimate monolithic code (some classes do not subdivide
— `62.01Z`, `70.22Z`, `82.99Z`) *or* a NACE Rev2-formatted output the
parser produced for a class that *does* subdivide (`10.71` →
`A/B/C/D`). The rewrite is symmetric: a legitimate monolithic `Z`
becomes the 5-char prefix, but the only NAF code under that prefix is
the `Z` itself so no extra rows are admitted; an erroneous `Z` becomes
the same prefix and admits the full NAF subdivision — the correct
result. We trade the failure signal for end-to-end correctness on the
dominant empirical failure mode.

The cost is one regex match per ICP NAF entry and a small expansion
of the surface that gets quietly absorbed: a parser hallucinating a
truly invalid code shaped `XX.XXZ` (where neither monolithic-NAF nor
NACE-confusion explains it) now masquerades as the prefix instead of
returning zero. We accept this in exchange for the empirical recovery
of the boulangerie case from 0 to ~3.9k candidates without a model
swap. The eval set will measure whether this changes precision in
practice.

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
