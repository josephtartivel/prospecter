"""
Compute Cohen's kappa between bootstrap (Sonnet) labels and human labels.

Drop this file at: eval/kappa.py

Usage:
    uv run python -m eval.kappa \
        --bootstrap eval/labels_bootstrap/ \
        --human eval/labels/

Outputs the agreement rate, raw accuracy, and a confusion matrix per
class. Cohen's kappa accounts for chance agreement; raw accuracy does
not. For 3-class labeling, expect kappa in the 0.65–0.80 range if your
rubric is well-defined.

Implementation note: pure stdlib, no scikit-learn dependency. The
formula is small enough to inline and verify.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

console = Console()
app = typer.Typer()


def cohens_kappa(rater_a: list[int], rater_b: list[int]) -> float:
    """Cohen's kappa for two raters with categorical labels."""
    assert len(rater_a) == len(rater_b), "raters must produce same number of labels"
    n = len(rater_a)
    if n == 0:
        return float("nan")

    classes = sorted(set(rater_a) | set(rater_b))
    # Observed agreement
    po = sum(1 for a, b in zip(rater_a, rater_b) if a == b) / n
    # Expected agreement (chance)
    count_a = Counter(rater_a)
    count_b = Counter(rater_b)
    pe = sum((count_a[c] / n) * (count_b[c] / n) for c in classes)
    if pe == 1.0:
        return 1.0  # perfect chance agreement, undefined otherwise
    return (po - pe) / (1 - pe)


@app.command()
def main(
    bootstrap: Path = typer.Option(..., help="Directory of bootstrap label JSONs."),
    human: Path = typer.Option(..., help="Directory of human-reviewed label JSONs."),
) -> None:
    bootstrap_files = {p.stem: p for p in bootstrap.glob("*.json")}
    human_files = {p.stem: p for p in human.glob("*.json")}
    common = sorted(set(bootstrap_files) & set(human_files))

    if not common:
        console.print("[red]No common ICPs between the two directories.[/]")
        raise typer.Exit(1)

    sonnet_labels: list[int] = []
    human_labels: list[int] = []

    for icp_id in common:
        b_data = json.loads(bootstrap_files[icp_id].read_text())
        h_data = json.loads(human_files[icp_id].read_text())
        b_by_siren = {c["siren"]: c["bootstrap_label"] for c in b_data["candidates"]}
        for c in h_data["candidates"]:
            siren = c["siren"]
            if siren not in b_by_siren:
                continue
            b_lbl = b_by_siren[siren]
            h_lbl = c["label"]
            if b_lbl is None or h_lbl is None:
                continue
            sonnet_labels.append(int(b_lbl))
            human_labels.append(int(h_lbl))

    n = len(sonnet_labels)
    if n == 0:
        console.print("[red]No matched (siren, icp) pairs across the two sets.[/]")
        raise typer.Exit(1)

    kappa = cohens_kappa(sonnet_labels, human_labels)
    accuracy = sum(1 for a, b in zip(sonnet_labels, human_labels) if a == b) / n

    console.print(f"\n[bold]Cohen's κ[/]: {kappa:.3f}")
    console.print(f"[bold]Raw accuracy[/]: {accuracy:.1%}")
    console.print(f"[bold]Pairs[/]: {n}\n")

    # Confusion matrix
    classes = sorted(set(sonnet_labels) | set(human_labels))
    table = Table(title="Confusion (rows=Sonnet, cols=Human)", show_header=True)
    table.add_column("Sonnet \\ Human", style="bold")
    for c in classes:
        table.add_column(str(c), justify="right")

    for s in classes:
        row = [str(s)]
        for h in classes:
            count = sum(1 for a, b in zip(sonnet_labels, human_labels) if a == s and b == h)
            row.append(str(count))
        table.add_row(*row)
    console.print(table)

    # Direction of disagreement
    over = sum(1 for s, h in zip(sonnet_labels, human_labels) if s > h)
    under = sum(1 for s, h in zip(sonnet_labels, human_labels) if s < h)
    console.print(
        f"\n[dim]Disagreement direction: Sonnet over-rates {over} times, "
        f"under-rates {under} times.[/]"
    )

    # Suggest README sentence
    console.print("\n[bold]README sentence to copy[/]:\n")
    console.print(
        f'  > Labels were bootstrapped by claude-sonnet-4-5 and human-reviewed.\n'
        f'  > Inter-annotator agreement (Cohen\'s κ) between Sonnet and human:\n'
        f'  > **{kappa:.2f}** on {n} pairs across {len(common)} ICPs.\n'
    )


if __name__ == "__main__":
    app()
