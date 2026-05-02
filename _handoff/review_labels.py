"""
CLI to human-review bootstrap labels, one (icp, candidate) at a time.

Drop this file at: eval/review_labels.py

Usage:
    # Review one ICP
    uv run python -m eval.review_labels --icp saas-paris-mid-market

    # Review all ICPs sequentially (resumable — skips already-reviewed pairs)
    uv run python -m eval.review_labels --all

Behavior:
  * Reads eval/labels_bootstrap/{icp_id}.json
  * For each candidate, shows ICP, company info, bootstrap label, raw response
  * You press: [Enter] keep / [0/1/2] override / [s] skip for now / [q] quit
  * Saves to eval/labels/{icp_id}.json with `agreed` flag and `human_label`
  * Resumable: re-running picks up where you left off
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

console = Console()
app = typer.Typer()

BOOTSTRAP_DIR = Path("eval/labels_bootstrap")
HUMAN_DIR = Path("eval/labels")


def _load_existing(icp_id: str) -> dict[str, dict]:
    """Return dict siren -> reviewed entry, for resumability."""
    path = HUMAN_DIR / f"{icp_id}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {c["siren"]: c for c in data.get("candidates", [])}


def _save(icp_id: str, icp_text: str, labeler_model: str, reviewed: dict[str, dict]) -> None:
    HUMAN_DIR.mkdir(parents=True, exist_ok=True)
    path = HUMAN_DIR / f"{icp_id}.json"
    payload = {
        "icp_id": icp_id,
        "icp_text": icp_text,
        "labeler_model": labeler_model,
        "human_reviewer": "joseph",
        "candidates": list(reviewed.values()),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def _review_one_icp(icp_id: str) -> bool:
    """Returns False if user quit."""
    bootstrap_path = BOOTSTRAP_DIR / f"{icp_id}.json"
    if not bootstrap_path.exists():
        console.print(f"[red]No bootstrap file for {icp_id}[/]")
        return True

    bootstrap = json.loads(bootstrap_path.read_text())
    icp_text = bootstrap["icp_text"]
    labeler_model = bootstrap.get("labeler_model", "unknown")
    reviewed = _load_existing(icp_id)

    candidates = bootstrap["candidates"]
    pending = [c for c in candidates if c["siren"] not in reviewed]

    if not pending:
        console.print(f"[green]✓[/] {icp_id}: all {len(candidates)} already reviewed.")
        return True

    console.print(Panel(f"[bold]ICP[/]: {icp_text}\n[dim]{icp_id} — {len(pending)} pending / {len(candidates)} total[/]", border_style="cyan"))

    for i, cand in enumerate(pending, start=1):
        c = cand["company"]
        bl = cand["bootstrap_label"]

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column(style="dim", width=14)
        table.add_column()
        table.add_row("Name", str(c.get("name", "")))
        table.add_row("NAF", f"{c.get('naf', '')} — {c.get('naf_label', '')}")
        table.add_row("City", str(c.get("city", "") or "—"))
        table.add_row("Headcount", str(c.get("headcount_tranche", "") or "—"))
        table.add_row("Created", str(c.get("creation_date", "") or "—"))
        table.add_row("SIREN", c["siren"] if "siren" in c else cand["siren"])

        bl_color = {0: "red", 1: "yellow", 2: "green"}.get(bl, "white")
        console.print()
        console.print(f"[bold]{i}/{len(pending)}[/]  →  bootstrap label: [{bl_color}]{bl}[/]")
        console.print(table)

        choice = Prompt.ask(
            "[Enter]=keep  [0/1/2]=override  [s]=skip  [q]=quit",
            default="",
            show_default=False,
        ).strip().lower()

        if choice == "q":
            _save(icp_id, icp_text, labeler_model, reviewed)
            console.print(f"[yellow]Saved progress to {HUMAN_DIR / f'{icp_id}.json'}[/]")
            return False
        if choice == "s":
            continue

        if choice in ("0", "1", "2"):
            human_label = int(choice)
        else:
            human_label = bl

        reviewed[cand["siren"]] = {
            "siren": cand["siren"],
            "label": human_label,
            "bootstrap_label": bl,
            "agreed": human_label == bl,
            "comment": None,
        }

        # Save every 10 entries to be resilient against crashes
        if i % 10 == 0:
            _save(icp_id, icp_text, labeler_model, reviewed)

    _save(icp_id, icp_text, labeler_model, reviewed)
    n_kept = sum(1 for r in reviewed.values() if r["agreed"])
    console.print(
        f"\n[green]✓[/] {icp_id} done — kept {n_kept} / {len(reviewed)} bootstrap labels."
    )
    return True


@app.command()
def main(
    icp: str = typer.Option(None, help="Specific ICP id to review."),
    all: bool = typer.Option(False, "--all", help="Review every ICP file in bootstrap dir."),
) -> None:
    if all:
        files = sorted(BOOTSTRAP_DIR.glob("*.json"))
        console.print(f"[bold]Reviewing {len(files)} ICPs[/]")
        for f in files:
            keep_going = _review_one_icp(f.stem)
            if not keep_going:
                break
    elif icp:
        _review_one_icp(icp)
    else:
        console.print("[red]Pass --icp <id> or --all.[/]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
