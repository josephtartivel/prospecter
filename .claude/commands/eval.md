---
description: "Run the prospecter eval pipeline end-to-end and surface the result deltas"
argument-hint: "[config-name|all]  default: all"
allowed-tools: ["Bash", "Read"]
---

You are running the Prospecter evaluation pipeline. The user wants the
delta numbers, not the raw logs.

Steps:

1. Confirm the SIRENE data is present:
   `ls -lh data/sirene/sirene.parquet 2>/dev/null || echo MISSING`
   If missing, stop and tell the user to run `./scripts/fetch_sirene.sh`
   and re-run this command.

2. Confirm labels exist:
   `ls eval/labels/ | wc -l`
   If zero, stop and tell the user to run the bootstrap-and-review
   sequence first (see `eval/README.md`).

3. Run the eval. The argument:
   - "all" or empty: `uv run python -m eval.runner --configs eval/configs/*.yaml`
   - otherwise: `uv run python -m eval.runner --configs eval/configs/$ARGUMENTS.yaml`

   Stream output. Don't paraphrase — let the user see the Rich table.

4. After completion, read `eval/reports/latest.json` and produce a
   summary in this exact format:

   ```
   ## Eval result — <date>

   | Config | P@10 | NDCG@10 | $/ICP | p95 ms |
   | ------ | ---- | ------- | ----- | ------ |
   | ...    | ...  | ...     | ...   | ...    |

   ### Notable deltas vs Sonnet
   - <one line per config: P@10 delta in pts, cost ratio>

   ### Worst-performing ICPs
   - <id> — <P@10> — <one-line guess at why it failed>
   ```

5. Suggest, but do not run, the next action:
   - If results changed: "ready to update README Results table — run
     `/spec update README results` to plan the diff"
   - If results match the previous run: "no change. consider closing
     the eval cycle."

Hard rules:
- Never edit `eval/labels/*` or `eval/reports/*` from this command.
  These are read-only outputs.
- Never invent a number. If a value is missing in the report, leave
  the cell empty in the summary table.
