# prospecter

Multi-agent prospector over the SIRENE registry. Takes a one-sentence
description of an ideal customer profile and returns a ranked CSV of
candidate French companies with per-row reasons.

## Setup

```bash
uv sync --extra app --extra dev
cp .env.example .env   # set at least one provider key
./scripts/fetch_sirene.sh
```

## Run

```bash
uv run prospecter run \
  "mid-size restaurants in Paris, 10-49 employees, opened in the last 5 years"
```

Streamlit UI: `uv run streamlit run app/streamlit_app.py`.

## Pipeline

```
ICPParser  ->  Search  ->  Scorer  ->  CSV
```

- `ICPParser` turns the natural-language ICP into a typed `ICP` via
  tool-use. Retries on validation error.
- `Search` runs a parameterised DuckDB query over the SIRENE bulk dump,
  returns up to 1000 rows sorted by creation date.
- `Scorer` rates each candidate 1-5 on NAF, headcount, geography and age,
  with a one-line reason. Runs in parallel.

LangGraph wires the three nodes over a typed `RunState`. Model calls go
through LiteLLM, so the same code runs against Anthropic, OpenAI,
DeepSeek, Mistral or a vLLM endpoint; the provider is a YAML field, not
a code change.

`SPEC.md` has the contracts. `DECISIONS.md` says why each piece looks the
way it does, with the alternatives that were considered and rejected.

## Eval

30 hand-labeled ICPs, top-50 candidates each, labels in {0, 1, 2}.
Metrics in `eval/metrics.py`: P@10, NDCG@10, total cost, p50/p95
latency. Labeling protocol in `eval/README.md`.

```bash
uv run python -m eval.runner --configs eval/configs/*.yaml
```

Baseline cost on Mistral (`icp-001`, top_n=50, parse + score): **$0.0022 / ICP**.

## Observability

Every LLM call is captured by Langfuse with cost, latency, token counts,
and the full prompt/response. All calls of one prospecter run share a
single trace, keyed by `RunState.run_id`. Set `LANGFUSE_ENABLED=true`
plus the public/secret keys in `.env` to opt in; unset leaves the
wrapper byte-identical to a plain LiteLLM call.

![langfuse trace](docs/langfuse_trace.png)

## Results

| Configuration                | P@10 | NDCG@10 |  $/ICP | p95 |
|------------------------------|-----:|--------:|-------:|----:|
| Filters only                 |  tbd |     tbd | $0.000 | tbd |
| + scorer · claude-haiku-4-5  |  tbd |     tbd |    tbd | tbd |
| + scorer · claude-sonnet-4-5 |  tbd |     tbd |    tbd | tbd |
| + scorer · gpt-4o-mini       |  tbd |     tbd |    tbd | tbd |
| + scorer · deepseek-chat     |  tbd |     tbd |    tbd | tbd |

## Notes

SIRENE is legal-entity data only; no personal-data lookups anywhere in
the pipeline.

Headcount is stored as INSEE tranche codes, not exact counts. ICPs
phrased with specific numbers (`around 30 employees`) get bucketed
conservatively to the surrounding tranches.

Tool-use is the default for structured output. A JSON-mode fallback
lives in `agents/scorer.py` for models that handle tools poorly.

## License

MIT.
