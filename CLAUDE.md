# Prospecter — Claude Code project rules

Project-specific invariants. Inherits from global rules in
`~/.claude/CLAUDE.md`. Where they conflict, this file wins.

## What this project is
Multi-agent B2B prospector over the French SIRENE registry. Takes a
one-sentence ICP, returns a ranked CSV of candidate companies with a
per-row reason. Local-first, single-user, Python.

Read in this order before starting any work:
1. `SPEC.md` — contracts and goals
2. `DECISIONS.md` — ADR index
3. `PROMPTS.md` — scoped build sessions, where the work plan lives
4. `notes/000N-*.md` — detailed ADRs

## Build / test / lint commands
- `uv sync` — install / update deps
- `uv run pytest` — run tests
- `uv run ruff check` — lint
- `uv run ruff format` — format
- `uv run pyright` — type check
- `uv run prospecter run "..."` — CLI entry point
- `uv run streamlit run app/streamlit_app.py` — demo UI
- `uv run python -m eval.runner --configs eval/configs/*.yaml` — eval

Always run `ruff check` + `pyright` + `pytest` before declaring a task
done. Read the actual output, never assume green.

## Stack invariants — do not deviate without an ADR
- Orchestration: LangGraph. Not LangChain.
- Model surface: LiteLLM. Never import `anthropic` / `openai` SDKs
  directly; the abstraction is the whole point.
- Schemas: Pydantic v2. Single source of truth for ICP, Company, Score,
  Lead, RunState, TraceEvent.
- Data: DuckDB over the SIRENE bulk dump. No Postgres, no SQLite.
- Structured output: tool-use everywhere. No JSON-mode parsing
  (fallback exists in `agents/scorer.py` but is the exception).
- Prompts: versioned `.md` files in `prompts/`. Never f-string
  prompts in Python.
- Dep manager: uv. No pip, no poetry, no conda.

## Forbidden patterns (signal of a junior or AI mistake)
- f-string SQL anywhere — always parameterised (`$param` style)
- Reading or writing `.env*` files
- Adding LangChain (only LangGraph + LiteLLM)
- AI-attribution trailers in commit messages
- Marketing voice anywhere ("amazing", "powerful", "robust")
- Emoji in code, prompts, or commits
- New top-level dependency without an updated ADR
- Streamlit UI features beyond what ADR-004 scopes (no auth, no
  sessions, no CSS frameworks)

## Voice
- Comments explain *why*, never *what*
- Type hints at function boundaries only
- Tests cover deterministic logic; agents are covered by the eval
- Commit messages: terse, lowercase, no periods, no AI trailers

## Eval and labeling
- Hand-labeled eval set in `eval/labels/`, bootstrapped via
  `eval/bootstrap_labels.py` (Sonnet) then human-reviewed via
  `eval/review_labels.py`. Cohen's kappa via `eval/kappa.py`.
- Never edit labels via prompt; always via the review CLI.
- Results table in README must reflect the latest run; no "tbd" once
  numbers exist.

## Out of scope (explicit non-goals)
- CRM features
- LinkedIn / personal-data lookups
- Multi-tenant / auth / accounts
- Fine-tuning
- Production deployment (Docker, K8s, cloud)

If a session drifts toward any of these, stop and confirm with the
user. Don't expand scope silently.

## Where the build plan lives
`PROMPTS.md` has the scoped sessions (1-9 plus 5b). One terminal per
session. One commit (max two) per session. If a session is sliding
past three commits, scope is out of hand.

`_handoff/` contains drop-in specs and code that specific sessions
read, adapt, and `git rm` once consumed. The folder shrinks toward
empty as the project completes.
