"""
Bootstrap eval labels with Claude Sonnet, then human-review.

Drop this file at: eval/bootstrap_labels.py

Usage:
    # 1. Generate top-50 candidates per ICP (run search only, no scoring)
    uv run python -m eval.gather_candidates --icps eval/icps.jsonl --out eval/candidates/

    # 2. Bootstrap labels with Sonnet
    uv run python -m eval.bootstrap_labels \
        --candidates eval/candidates/ \
        --icps eval/icps.jsonl \
        --out eval/labels_bootstrap/

    # Estimated cost: ~$9 for 30 ICPs × 50 candidates @ Sonnet pricing.

This produces eval/labels_bootstrap/{icp_id}.json. Then run review_labels.py
to human-validate, then kappa.py to measure agreement.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from prospecter import llm  # your wrapped LiteLLM client

console = Console()
logger = logging.getLogger(__name__)

app = typer.Typer()


RUBRIC = """\
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

Be strict. When in doubt between 1 and 2, choose 1. When in doubt
between 0 and 1, choose 0.

Respond with the integer 0, 1, or 2 only. No other text.
"""


@dataclass(slots=True)
class LabelTask:
    icp_id: str
    icp_text: str
    siren: str
    company: dict
    bootstrap_label: int | None = None
    raw_response: str | None = None


def _parse_label(text: str) -> int | None:
    """Defensive parse: model may add stray punctuation despite instructions."""
    for ch in text.strip():
        if ch in "012":
            return int(ch)
    return None


async def _label_one(task: LabelTask, model: str, semaphore: asyncio.Semaphore) -> LabelTask:
    async with semaphore:
        prompt = RUBRIC.format(
            icp_text=task.icp_text,
            name=task.company.get("name", ""),
            naf=task.company.get("naf", ""),
            naf_label=task.company.get("naf_label", ""),
            city=task.company.get("city", "") or "—",
            headcount_tranche=task.company.get("headcount_tranche", "") or "—",
            creation_date=task.company.get("creation_date", "") or "—",
        )
        try:
            response = await llm.call(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                run_id=f"bootstrap-{task.icp_id}",
                agent_name="bootstrap_labeler",
                icp_id=task.icp_id,
                max_tokens=4,  # we only want one digit
            )
            text = response.choices[0].message.content or ""
            task.raw_response = text
            task.bootstrap_label = _parse_label(text)
        except Exception as exc:
            logger.exception("Failed to label %s/%s: %s", task.icp_id, task.siren, exc)
            task.bootstrap_label = None
        return task


@app.command()
def main(
    candidates: Path = typer.Option(..., help="Directory of {icp_id}.json candidate files."),
    icps: Path = typer.Option(..., help="JSONL of ICPs."),
    out: Path = typer.Option(..., help="Output directory for bootstrap labels."),
    model: str = typer.Option("claude-sonnet-4-5", help="Labeler model."),
    concurrency: int = typer.Option(8, help="Max concurrent label calls."),
) -> None:
    out.mkdir(parents=True, exist_ok=True)

    # Index ICPs by id
    icp_by_id: dict[str, str] = {}
    with icps.open() as f:
        for line in f:
            row = json.loads(line)
            icp_by_id[row["icp_id"]] = row["icp_text"]

    # Build task list
    tasks: list[LabelTask] = []
    for icp_file in sorted(candidates.glob("*.json")):
        icp_id = icp_file.stem
        if icp_id not in icp_by_id:
            console.print(f"[yellow]Skipping {icp_id}: not in ICP file[/]")
            continue
        data = json.loads(icp_file.read_text())
        for cand in data["candidates"]:
            tasks.append(
                LabelTask(
                    icp_id=icp_id,
                    icp_text=icp_by_id[icp_id],
                    siren=cand["siren"],
                    company=cand,
                )
            )

    console.print(f"[bold]Labeling {len(tasks)} (icp, candidate) pairs with {model}...[/]")

    semaphore = asyncio.Semaphore(concurrency)

    async def _run() -> list[LabelTask]:
        results: list[LabelTask] = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            pbar = progress.add_task("labeling", total=len(tasks))
            coros = [_label_one(t, model, semaphore) for t in tasks]
            for fut in asyncio.as_completed(coros):
                done = await fut
                results.append(done)
                progress.advance(pbar)
        return results

    labeled = asyncio.run(_run())

    # Group by ICP, persist
    by_icp: dict[str, list[LabelTask]] = {}
    for t in labeled:
        by_icp.setdefault(t.icp_id, []).append(t)

    for icp_id, items in by_icp.items():
        path = out / f"{icp_id}.json"
        payload = {
            "icp_id": icp_id,
            "icp_text": icp_by_id[icp_id],
            "labeler_model": model,
            "candidates": [
                {
                    "siren": t.siren,
                    "company": t.company,
                    "bootstrap_label": t.bootstrap_label,
                    "raw_response": t.raw_response,
                }
                for t in items
            ],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        n_failed = sum(1 for t in items if t.bootstrap_label is None)
        console.print(
            f"[green]✓[/] {icp_id}: {len(items)} labeled, "
            f"{n_failed} failed → {path}"
        )

    total_failed = sum(1 for t in labeled if t.bootstrap_label is None)
    console.print(
        f"\n[bold]Done.[/] {len(labeled) - total_failed} / {len(labeled)} labeled. "
        f"Next: human-review with [cyan]uv run python -m eval.review_labels[/]."
    )


if __name__ == "__main__":
    app()
