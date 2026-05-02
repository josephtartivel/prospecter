# Labeling protocol — bootstrap with Sonnet, validate with human

The honest goal: produce 30 ICPs × top-50 = 1,500 ground-truth labels in
3-4 hours of human time, not 12.

## Why this matters

Without labels, your Results table stays "tbd" and the project doesn't
deliver on its central pitch. The naive "label everything by hand" plan
is 12 hours of focused, soul-crushing work that most people abandon
halfway through. The bootstrap-then-review approach is 70% faster *and*
gives you a senior signal: you measured the agreement between LLM and
human labels (Cohen's kappa), so your eval is calibrated, not assumed.

## Label schema

Three classes, deliberately small:

| Label | Meaning |
|-------|---------|
| 0 | Not a fit. SDR would skip. |
| 1 | Plausible but uncertain. SDR would research more. |
| 2 | Strong fit. SDR would call this week. |

Saved as `eval/labels/{icp_id}.json`:

```json
{
  "icp_id": "saas-paris-mid-market",
  "icp_text": "B2B SaaS companies in Paris, 50-200 employees, 5+ years old",
  "labeled_at": "2026-05-04",
  "labeler": "joseph + sonnet-bootstrap",
  "candidates": [
    {"siren": "12345...", "label": 2, "bootstrap_label": 2, "agreed": true,  "comment": null},
    {"siren": "67890...", "label": 1, "bootstrap_label": 2, "agreed": false, "comment": "wrong NAF"}
  ]
}
```

## Protocol

### Step 1 — Generate top-50 candidates per ICP (no labels yet)
Run the search agent only (no scorer):
```bash
uv run python -m eval.gather_candidates --icps eval/icps.jsonl \
    --out eval/candidates/
```

### Step 2 — Bootstrap labels with Sonnet
Use the included `bootstrap_labels.py` (drop into `eval/` directory). It
runs Claude Sonnet over each `(icp, candidate)` pair with the rubric
below, writes `eval/labels_bootstrap/{icp_id}.json`.

Cost estimate: 1,500 calls × ~400 tokens out × $15/Mtoken (Sonnet output)
= ~$9 total. Cheap.

### Step 3 — Human review with the included CLI
```bash
uv run python -m eval.review_labels --icp saas-paris-mid-market
```
Shows you each candidate with the bootstrap label and asks: keep / change.
You only need to think about the cases that look wrong. ~3 minutes per
ICP × 30 ICPs = 90 min, plus a deeper second pass = ~3-4 hours total.

### Step 4 — Compute Cohen's kappa
```bash
uv run python -m eval.kappa --bootstrap eval/labels_bootstrap/ \
    --human eval/labels/
```
Outputs the agreement rate between Sonnet and you. Expect 0.65 - 0.80
on this kind of task.

**This number goes in the README.** Example sentence:

> Labels were bootstrapped by claude-sonnet-4-5 and human-reviewed.
> Inter-annotator agreement (Cohen's κ) between Sonnet and human:
> **0.74** on the validation set, indicating substantial but not perfect
> agreement; the human pass mostly downgraded over-eager 2's to 1's.

That sentence is what makes a senior reviewer pause and respect the
project. It says: *I know LLM-as-judge is imperfect, I measured how
imperfect, and I documented my correction protocol.*

## Bootstrap rubric (used by Sonnet)

```
You are labeling whether a French company matches an SDR's Ideal
Customer Profile (ICP). Output exactly one of: 0, 1, 2.

ICP: {icp_text}

Company:
- Name: {name}
- NAF code: {naf} ({naf_label})
- City: {city}
- Headcount tranche: {headcount_tranche}
- Date created: {creation_date}

Labels:
- 2 (strong fit): all hard constraints from the ICP are met (industry,
  region, size, age). An SDR would call this prospect this week.
- 1 (plausible): most constraints are met but at least one is borderline
  (adjacent NAF, edge of headcount band, missing data). An SDR would
  research more before calling.
- 0 (no fit): at least one hard constraint is clearly violated. An SDR
  would skip.

Be strict. When in doubt between 1 and 2, choose 1. When in doubt between
0 and 1, choose 0.

Respond with the integer 0, 1, or 2 only.
```

## Why this protocol is itself a senior signal

1. **It is honest about LLM-as-judge limitations.** A junior writes
   "we used Sonnet to label". A senior measures kappa, documents the
   correction direction, and ships both numbers.
2. **It is reproducible.** Anyone can rerun the bootstrap, see the
   diff with the human labels, and decide if your eval is trustworthy.
3. **It is cost-aware.** $9 to label vs 12 hours of human time. The
   tradeoff is explicit, not silent.
4. **It mirrors a real production pattern.** This is exactly the kind
   of bootstrap-then-validate loop production AI teams use to ship
   eval datasets without burning a quarter on human labels.

Add a short version of this section to `eval/README.md`. It will be
read.
