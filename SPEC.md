# prospecter — technical spec

Source of truth for the build. Read this before adding new components or
making architectural changes. Update it when contracts change.

---

## 1. Problem

B2B SaaS sales teams hand-build prospect lists by reading the SIRENE registry
through search UIs and copying matches into spreadsheets. The workflow:

1. SDR reads a sales playbook ICP description.
2. SDR opens `annuaire-entreprises.data.gouv.fr` or a paid tool, filters
   by NAF code / region / headcount, eyeballs results.
3. SDR copies promising companies into a Google Sheet.
4. SDR ranks intuitively, hands top 20 to AEs.

Steps 1–4 are slow, inconsistent across SDRs, and don't preserve why a lead
was rated highly. Result: AEs work uneven lists.

## 2. Goal

Given a single-sentence ICP, return a CSV of up to 50 ranked candidate
companies from SIRENE, each with a `score ∈ {1..5}` and a one-line `reason`,
in under 30 seconds wall-clock and under $0.05 per ICP on the cheap-model
configuration (Haiku / gpt-4o-mini / deepseek-chat).

## 3. Non-goals

- **Not a CRM.** No persistence beyond the CSV output.
- **Not a personal-data tool.** SIRENE is legal-entity data only — no
  individual contact enrichment, ever. This is a hard line.
- **Not multi-tenant.** Single-user CLI/UI. No auth.
- **Not language-flexible.** ICPs may be written in French or English;
  output rows always include the SIRENE-canonical French fields.
- **Not real-time.** SIRENE is updated quarterly by INSEE; we treat the
  bulk dump as static for the lifetime of a run.
- **Not provider-locked.** The system runs against any LiteLLM-supported
  model; eval ablates across at least two providers.

## 4. Users

- **The primary user is the project author**, demonstrating senior-grade
  AI engineering. Realistic secondary users:
  - SDR / sales-ops engineer at a French B2B SaaS who'd run this on a
    laptop to seed a campaign.
  - Recruiter / hiring manager evaluating the work.

## 5. Architecture

```
                 ┌────────────────┐
   NL ICP ─────► │   ICPParser    │ ─── tool_use ──► ICP (Pydantic)
                 └────────────────┘
                        │
                        ▼
                 ┌────────────────┐
                 │     Search     │ ─── DuckDB query ──► list[Company] (≤1000)
                 └────────────────┘
                        │
                        ▼
                 ┌────────────────┐
                 │     Scorer     │ ─── parallel calls ──► list[Score]
                 └────────────────┘
                        │
                        ▼
                  ranked CSV (top N=50)
```

Orchestration is a LangGraph `StateGraph[RunState]` where `RunState` is a
typed Pydantic dataclass holding the ICP, candidates, scores, and the
trace. Each node is a thin Python function calling one agent module.
Streaming events from the graph feed the Streamlit trace view.

Model calls go through `llm.py`, a LiteLLM-backed wrapper. The agent code
never imports `anthropic`, `openai`, etc. directly — that's the contract.

**Phase 2 (deferred)**: `Enricher` between Search and Scorer (web/website
signals); `Summarizer` after Scorer (gap analysis). Skeletons not in repo
yet — adding them before phase-1 eval is premature.

## 6. Data contracts (Pydantic, see `src/prospecter/schemas.py`)

### `ICP`

```python
class ICP(BaseModel):
    naf_codes: list[str] = []         # e.g. ["56.10A", "56.10C"]
    headcount_min: int | None = None  # SIRENE tranche min in employees
    headcount_max: int | None = None
    region_code: str | None = None    # INSEE region code (e.g. "11" for IDF)
    department_codes: list[str] = []  # INSEE department codes
    postal_codes: list[str] = []      # specific postcodes if user gave them
    age_max_months: int | None = None # max months since legal creation
    age_min_months: int | None = None
    legal_status_in: list[str] = []   # SIRENE statutDiffusionEtablissement, etc.
    require_active: bool = True       # filter to currently active entities

    @model_validator(mode="after")
    def at_least_one_filter(self): ...  # fail if all empty — prevents "match anything"
```

### `Company`

```python
class Company(BaseModel):
    siren: str                 # 9-digit, stable identifier
    siret_main: str            # primary establishment, 14 digits
    name: str                  # denominationUniteLegale
    naf_code: str              # activitePrincipaleUniteLegale
    headcount_tranche: str     # SIRENE tranche code, "00" through "53"
    headcount_label: str       # human label, e.g. "10 to 19"
    region_code: str
    department_code: str
    postal_code: str
    commune: str
    creation_date: date
    is_active: bool
```

### `Score`

```python
class Score(BaseModel):
    siren: str
    value: int = Field(ge=1, le=5)
    reason: str = Field(max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)
```

### `Lead` (the output row)

```python
class Lead(BaseModel):
    company: Company
    score: Score
```

### `RunState` (the LangGraph state)

```python
class RunState(BaseModel):
    nl_query: str
    icp: ICP | None = None
    candidates: list[Company] = []
    scores: list[Score] = []
    trace: list[TraceEvent] = []  # for Streamlit / debugging
    cost_cents: float = 0.0
    started_at: datetime
```

## 7. Agent contracts

### ICPParser

- **Input**: `nl: str`
- **Output**: `ICP`
- **Mechanism**: single LiteLLM call with one tool, `submit_icp(icp: ICP)`.
  The model "calls" the tool with arguments; we validate via Pydantic and
  return. Up to 2 retries on validation failure with the validation error
  message appended to the conversation.
- **System prompt**: `prompts/icp_parser_v1.md`. Includes a NAF code primer
  for the most common codes the project sees, headcount tranche reference,
  and INSEE region codes.
- **Token budget**: 4k input, 1k output. Anything over fails fast.
- **Latency target**: p95 < 3s on Haiku.

### Search

- **Input**: `ICP`
- **Output**: `list[Company]` (capped at 1000)
- **Mechanism**: deterministic DuckDB query against the SIRENE table,
  parameterised on ICP fields. No LLM. Returns sorted by `creation_date`
  descending — a sensible default that surfaces newer companies first.
- **Cost target**: $0.

### Scorer

- **Input**: `(ICP, Company)`
- **Output**: `Score`
- **Mechanism**: LiteLLM call with tool-use (`submit_score(score: Score)`).
  System prompt holds the rubric (cached on Anthropic; recomputed on others).
  Run in parallel via `asyncio` over the candidates, with a configurable
  concurrency cap (default 8 to avoid rate limits on free-tier keys).
- **System prompt**: `prompts/scorer_v1.md`. The rubric scores on:
  - **NAF alignment** (exact / adjacent / off)
  - **Headcount fit** (in range / off by one tranche / off)
  - **Geographic fit** (in region/department / adjacent / elsewhere in FR)
  - **Age fit** (within ICP window / borderline / outside)
- **Cost target** (per call):
  - Haiku / gpt-4o-mini / deepseek-chat: < $0.001
  - Sonnet: < $0.005
- **Latency target**: p95 < 1.5s per call on Haiku.

## 8. Eval methodology

See `eval/README.md` for the full labeling protocol. Summary:

- **Eval set**: 30 ICP queries across three difficulty tiers (10/10/10).
  Difficulty = number of constraints + ambiguity in the natural language.
- **Labels**: for each ICP, the top 50 candidates returned by the unscored
  filter step are hand-labeled `0` (off-ICP), `1` (acceptable), `2`
  (clear fit). Stored as JSON per ICP under `eval/labels/`.
- **Metrics**:
  - **P@10**: of top-10 by scorer, fraction with label ≥ 1.
  - **NDCG@10**: standard DCG using labels as gains.
  - **Cost**: total $ for the 30-ICP run, broken down by agent and model.
  - **Latency**: end-to-end p50/p95 wall-clock per ICP.
- **Provider ablations** (must be in the README):
  - Filters only (no scorer) — the floor.
  - claude-haiku-4-5 (Anthropic, cheap default)
  - claude-sonnet-4-5 (Anthropic, premium)
  - gpt-4o-mini (OpenAI, comparison)
  - deepseek-chat (DeepSeek, cheapest)

## 9. Budgets

| Resource | Per ICP (Haiku) | Per ICP (Sonnet) | Per 30-ICP run |
|---|---:|---:|---:|
| Cost | < $0.05 | < $0.20 | < $1.50 (Haiku) / $6 (Sonnet) |
| Wall-clock | < 30s | < 30s | < 15 min |
| Model calls | ≤ 51 (1 parser + 50 scorer) | ≤ 51 | ≤ 1530 |

If a run exceeds these, that's a signal — investigate, don't move budgets.

## 10. Failure modes & mitigations

| Failure | Detection | Mitigation |
|---|---|---|
| ICP parser returns `at_least_one_filter` violation | Pydantic raises | Retry w/ error message in conv (max 2 attempts) |
| Search returns 0 candidates | empty list | Surface to user; suggest broadening (which fields are most restrictive) |
| Search returns >1000 candidates | length check | Truncate, log a warning, sort by `creation_date desc` |
| Scorer returns invalid score | Pydantic raises | Retry once; if fail again, drop with `reason="scoring failed"` |
| LiteLLM provider rate-limit | 429 | tenacity exponential backoff, 5 attempts |
| Provider API down | 5xx repeatedly | Fail the run; emit partial CSV with whatever scored so far |
| Tool-use unsupported on a chosen model | LiteLLM raises | Fall back to JSON-mode prompt (documented in scorer agent) |

## 11. Out of scope (explicit list)

- Personal data, contact enrichment, LinkedIn lookups, email guessing.
- Live SIRENE API integration (we use the bulk dump).
- Authentication, multi-tenant, role-based access.
- Streaming UI updates beyond what LangGraph exposes by default.
- Fine-tuning. The whole point is that prompting + eval is enough.
- Production deployment (Docker, K8s, autoscale). Local-first, on purpose.

## 12. Future work (parking lot, not commitments)

- **`Enricher` agent** that pulls company website + recent press signals
  for top-K only, before scoring.
- **`Summarizer` agent** that produces ICP-gap analysis ("only 3 candidates
  in 75011, ICP may be over-constrained on region").
- **Self-correction loop** in `ICPParser` — if Search returns 0 or >5000,
  re-query the parser with that signal.
- **Sub-second mode** — pre-compute embeddings of all SIRENE company names
  + descriptions, replace LLM scorer with a hybrid embedding+rubric scorer.
- **Local model run** — try `ollama/llama-3.1-8b-instruct` as the scorer
  to demonstrate the LiteLLM abstraction holds down to laptop-served models.
