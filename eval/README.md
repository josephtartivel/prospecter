# Eval

The eval set is the load-bearing piece of this project. Without it, every
claim in the README is unverifiable. Read this before adding ICPs or
running the harness.

## Structure

```
eval/
  icps.jsonl              one ICP per line (id, nl, difficulty, notes)
  labels/
    {icp_id}.json         per-ICP relevance labels (siren → 0/1/2)
  configs/
    haiku.yaml            model + agent settings for each ablation
    sonnet.yaml
    gpt-4o-mini.yaml
    deepseek-chat.yaml
  runner.py               runs the pipeline over the eval set, writes report
  metrics.py              P@K, NDCG@K, cost stats
  reports/
    {date}_{config}.json  one report per run
    latest.json           symlink to the most recent
```

## Eval set composition

30 ICPs across three difficulty tiers, 10 each:

- **Easy** — one or two clear constraints, unambiguous wording.
  *"French SaaS startups in Paris, less than 50 employees."*
- **Medium** — three or four constraints, mild ambiguity.
  *"Mid-size restaurants in Paris and inner suburbs, opened in the last 5 years."*
- **Hard** — multiple constraints, soft language, edge cases.
  *"Boutique consulting firms doing tech-adjacent work, 10–30 people, with
  a recent French regional presence outside Paris."*

The starter set in this repo has 5 ICPs (covers the difficulty tiers) so
the harness can run end-to-end before the full set is built. Expand to 30
following the protocol below.

## Labeling protocol

For each ICP:

1. Run the **filter-only** pipeline (no scorer) and capture the top 50
   candidates.
2. For each of the 50 candidates, assign a relevance label:
   - `2` — clear fit on every axis the ICP specifies.
   - `1` — acceptable fit; small mismatch on a secondary axis.
   - `0` — off-ICP; would not appear on a hand-picked list.
3. Save the labels as `eval/labels/{icp_id}.json` with shape
   `{"siren_to_label": {"123456789": 2, ...}, "labeled_at": "2026-05-..."}`.

Time budget: ~25 min per ICP × 30 = ~12 hours. Doable across a few
evenings. Don't shortcut this — the eval is what makes the project
credible, and labels-by-LLM defeats the purpose.

## Metrics

Computed by `metrics.py`:

- **P@10**: of the top 10 returned by the scorer, fraction with label ≥ 1.
- **NDCG@10**: standard DCG with labels as gains, normalised by IDCG.
- **Cost (USD)**: total `cost_usd` across LLM calls in the run.
- **Latency**: end-to-end wall-clock per ICP, p50 and p95.

The metrics are computed *over the union of (scorer_returned, labeled)*.
If the scorer returns a candidate that wasn't in the labeled top-50, it's
treated as label `0`. This is conservative — it penalises the scorer for
"discovering" candidates that didn't make the labeled set.

## Running

```bash
# whole matrix
uv run python -m eval.runner --configs eval/configs/*.yaml

# one config
uv run python -m eval.runner --configs eval/configs/haiku.yaml
```

Reports land in `eval/reports/`. The CLI prints a table and updates
`reports/latest.json`.

## Adding an ICP

1. Append a line to `eval/icps.jsonl` with shape:
   ```json
   {"id": "icp-006", "nl": "...", "difficulty": "medium", "notes": "..."}
   ```
2. Run the pipeline at filter-only level to get top-50 candidates.
3. Hand-label each candidate per the protocol above. Save under `labels/`.
4. Re-run the eval; check that headline P@10 doesn't drop unexpectedly.
   Surprises here mean the new ICP exposes a real failure mode — write it
   down in the README's "Where it fails" section.
