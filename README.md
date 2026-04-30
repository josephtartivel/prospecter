# prospecter

Multi-agent B2B prospector over the French SIRENE registry. You describe an
ideal customer profile in one sentence; a small system of agents turns that
into a structured query, runs it against ~30 million French legal entities,
scores each match against the ICP with an LLM-as-judge rubric, and hands back
a ranked CSV with one-line justifications per row.

It's a portfolio project, but it's the realistic prospecting workflow that
B2B SaaS GTM teams run by hand today — modeled on the kind of internal tool
you'd build for sales ops at a company like Skello, Pigment, or Spendesk.

## Demo

```bash
uv run prospecter run \
  "mid-size French restaurants in Paris, opened in the last 5 years, 10–49 employees"
```

Output (truncated):

```
ICP parsed in 1.2s — 1 NAF code, headcount 10–49, region 11, age ≤ 60mo
Search returned 312 candidates from 4.7M restaurants
Scoring top 50 (claude-haiku-4-5)…
Wrote 50 ranked leads → out/2026-05-01_paris-restaurants.csv
Cost: $0.013  ·  Latency: 11.2s  ·  p95 scoring call: 0.9s
```

The Streamlit UI (`uv run streamlit run app/streamlit_app.py`) shows the same
flow with the per-agent trace, fed by LangGraph's stream events.

## Results

Hand-labeled eval set: 30 ICPs × top-50 candidates each, three relevance
tiers (`0`/`1`/`2`). The eval ablates across providers — same agent code,
different model behind LiteLLM. Numbers below from `eval/reports/latest.json`.

> **Eval results are filled in once the eval set is complete and the runner
> has been executed end-to-end. See `eval/README.md` for the methodology and
> the labeling protocol.**

| Configuration               | P@10  | NDCG@10 | $/ICP  | p95 latency |
|-----------------------------|------:|--------:|-------:|------------:|
| Filters only (no scorer)    | _tbd_ |   _tbd_ | $0.000 |       _tbd_ |
| + Scorer · claude-haiku-4-5 | _tbd_ |   _tbd_ |  _tbd_ |       _tbd_ |
| + Scorer · claude-sonnet-4-5| _tbd_ |   _tbd_ |  _tbd_ |       _tbd_ |
| + Scorer · gpt-4o-mini      | _tbd_ |   _tbd_ |  _tbd_ |       _tbd_ |
| + Scorer · deepseek-chat    | _tbd_ |   _tbd_ |  _tbd_ |       _tbd_ |

The headline number is the cheap-vs-expensive gap. If Haiku, gpt-4o-mini, or
deepseek-chat reaches within 2 P@10 points of Sonnet, the system ships on
the cheap one — that's a >10× cost delta on the scorer.

## Architecture

Three agents, sequential, orchestrated as a LangGraph `StateGraph`.
`Enricher` and `Summarizer` are documented in `SPEC.md` but deferred to
phase two — adding them before the core eval runs would be premature.

```
NL ICP ──► ICPParser ──► Search ──► Scorer ──► ranked CSV
   (LiteLLM tool-use)  (DuckDB)   (LLM judge,
                                   rubric, parallel)
```

- **`ICPParser`** turns *"mid-size Paris restaurants founded after 2020"*
  into a typed `ICP` (`schemas.py`). Tool-use guarantees the output validates
  against the schema or the agent retries.
- **`Search`** translates the `ICP` into a parameterized DuckDB query against
  the SIRENE bulk dump and returns up to 1000 candidates.
- **`Scorer`** rates each candidate 1–5 against the ICP using a rubric in the
  system prompt, returning a `Score` with a one-line `reason`. Runs in
  parallel up to a concurrency cap.

Orchestration is a LangGraph `StateGraph` over a typed Pydantic `RunState`.
Model calls go through LiteLLM, so the same agent code runs against
Anthropic, OpenAI, DeepSeek, Mistral, or a locally-served vLLM endpoint —
that's how the multi-provider eval ablation works without code changes.

See `SPEC.md` for contracts, error cases, and budgets.

## Why this design (the senior bits)

- **Model-neutral by design, opinionated by ablation.** Agent code talks to
  LiteLLM, not a specific SDK. The eval picks the winning model on
  evidence; we don't pick by vendor allegiance.
- **LangGraph for orchestration, LiteLLM for calls.** The graph is for state
  + streaming + checkpointing; the model calls themselves stay on a single
  unified surface. Documented in `notes/0001-langgraph-stack.md`.
- **Haiku as the default, Sonnet as the documented escalation.** The cost
  story matters. The ablation is the proof.
- **Prompts are versioned files** in `prompts/`, loaded by `prompt_library`.
  Diffing v1 vs v2 in git is the unit of prompt iteration.
- **Tool-use for structured output**, not JSON mode. Pydantic schema is
  converted to a tool definition; the model "calls" the tool with arguments
  Pydantic validates. Cleaner failure mode than parsing JSON strings, and
  LiteLLM normalises tool-use across providers.
- **Cost & latency tracked from call zero.** Every model call goes through
  `llm.py`, which records `(model, in, out, cents, ms)`. Eval reports
  surface `$/ICP` and `p95`.
- **Prompt caching** on the SIRENE schema doc when the provider supports it
  (Anthropic `cache_control: ephemeral`). Real cost savings on a 30-ICP run.
- **Eval-first.** The 5-ICP starter set ships in this commit. The harness
  runs before the agents are fully integrated, so we never drift into
  "looks fine in a notebook" territory.

## Where it fails (honest)

This section gets filled in once the eval has run a few times. Current
suspected failure modes (to verify, not to claim yet):

- ICPParser sometimes confuses adjacent NAF codes (e.g. 56.10A traditional
  restaurants vs 56.10C fast food). Mitigation: include a small NAF disambig
  table in the system prompt.
- Scorer over-rates well-known brands because pre-training bleeds into the
  judgment. Mitigation: instruct the rubric to score only on observable ICP
  fields, and ablate.
- Headcount ranges in SIRENE are coarse `tranches` (`10-19`, `20-49`, …) —
  ICPs phrased with exact numbers ("around 30 employees") get bucketed
  conservatively.
- Tool-use on weaker models (some open-weight chat-tunes) may need a JSON
  fallback. Tracked in SPEC §10.

## Setup

```bash
# 1. Clone, install
git clone git@github.com:josephtartivel/prospecter.git
cd prospecter
uv sync --extra app --extra dev

# 2. Get the SIRENE bulk dump (see data/README.md, ~2 GB)
./scripts/fetch_sirene.sh

# 3. Set the API key for whichever provider(s) you'll use
cp .env.example .env  # then fill in at least one *_API_KEY

# 4. Smoke test
uv run prospecter run "Paris SaaS startups with 10-49 employees"
```

## Layout

```
src/prospecter/
  llm.py             one LiteLLM-backed client wrapper, usage tracking, retries
  schemas.py         ICP, Company, Score, Lead — Pydantic v2
  prompt_library.py  loads prompts/{name}_v{n}.md
  graph.py           LangGraph StateGraph wiring the three agents
  agents/
    icp_parser.py    NL → ICP via tool-use
    search.py        ICP → list[Company] via DuckDB (deterministic, no LLM)
    scorer.py        (ICP, Company) → Score, parallelised
  tools/
    duckdb_tool.py   the SIRENE query helper used by Search
  pipeline.py        end-to-end driver: build graph, run, write CSV
  cli.py             typer-based CLI
prompts/             versioned .md prompt files
eval/                ICPs, hand labels, runner, metrics, provider ablation
app/                 Streamlit demo with streamed agent trace
notes/               ADRs (architecture decisions)
SPEC.md              the source of truth for the build
DECISIONS.md         summary of major choices, with rejected alternatives
PROMPTS.md           scoped prompts to drive the build session-by-session
```

## License

MIT.
