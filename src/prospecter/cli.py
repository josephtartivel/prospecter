"""Typer CLI: `prospecter run "..."`."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from prospecter.pipeline import run as run_pipeline

app = typer.Typer(help="Multi-agent B2B prospector over the SIRENE registry.")
console = Console()


@app.command()
def run(
    nl_query: str = typer.Argument(..., help="One-sentence ICP description."),
    top_n: int = typer.Option(50, "--top", help="How many leads to keep."),
    output: Path = typer.Option(Path("out"), "--out", help="Directory for the CSV."),
    log_level: str = typer.Option("INFO", "--log", help="Logging level."),
):
    """Run the parse → search → score pipeline and write a ranked CSV."""
    load_dotenv()
    logging.basicConfig(
        level=log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    leads, state = run_pipeline(nl_query, output_dir=output, top_n=top_n)

    table = Table(title=f"Top leads — {nl_query[:60]}")
    table.add_column("Score")
    table.add_column("Conf")
    table.add_column("SIREN")
    table.add_column("Name", overflow="fold")
    table.add_column("Where")
    table.add_column("Reason", overflow="fold")
    for lead in leads[:10]:
        table.add_row(
            str(lead.score.value),
            f"{lead.score.confidence:.2f}",
            lead.company.siren,
            lead.company.name,
            f"{lead.company.commune} ({lead.company.postal_code})",
            lead.score.reason,
        )
    console.print(table)
    console.print(
        f"[dim]cost ≈ ${state.cost_cents / 100:.4f} · "
        f"candidates: {len(state.candidates)} · "
        f"scored: {len(state.scores)}[/dim]"
    )


@app.command()
def version():
    """Print the package version."""
    from prospecter import __version__

    console.print(__version__)


if __name__ == "__main__":
    app()
