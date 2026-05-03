"""End-to-end driver: build the graph, run it, write the CSV.

Skeleton — wires everything together. Implement in session 4 once the
search and scorer agents work.
"""

from __future__ import annotations

import csv
import logging
from datetime import UTC, datetime
from pathlib import Path

from prospecter.graph import build_graph
from prospecter.llm import LLM
from prospecter.schemas import Lead, RunState
from prospecter.tools.duckdb_tool import SireneStore

log = logging.getLogger(__name__)


def run(nl_query: str, *, output_dir: str | Path = "out", top_n: int = 50) -> tuple[list[Lead], RunState]:
    """Run the full pipeline. Returns the ranked top-N leads and the run state."""
    llm = LLM.from_env()
    store = SireneStore()
    app = build_graph(llm=llm, store=store)

    initial = RunState(nl_query=nl_query, started_at=datetime.now(tz=UTC))
    final: RunState = app.invoke(initial)  # type: ignore[assignment]

    leads = _build_leads(final, top_n=top_n)
    out_path = _write_csv(leads, output_dir=Path(output_dir), nl_query=nl_query)
    log.info("wrote %d leads to %s", len(leads), out_path)
    return leads, final


def _build_leads(state: RunState, *, top_n: int) -> list[Lead]:
    """Match scores back to companies and return the top-N as Leads."""
    by_siren = {c.siren: c for c in state.candidates}
    pairs = [
        Lead(company=by_siren[s.siren], score=s)
        for s in state.scores
        if s.siren in by_siren
    ]
    pairs.sort(key=lambda p: (p.score.value, p.score.confidence), reverse=True)
    return pairs[:top_n]


def _write_csv(leads: list[Lead], *, output_dir: Path, nl_query: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = "-".join(nl_query.lower().split())[:40].rstrip("-")
    today = datetime.now(tz=UTC).date().isoformat()
    path = output_dir / f"{today}_{slug}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "score",
                "confidence",
                "siren",
                "siret_main",
                "name",
                "naf_code",
                "headcount_label",
                "department_code",
                "postal_code",
                "commune",
                "creation_date",
                "reason",
            ]
        )
        for lead in leads:
            c, s = lead.company, lead.score
            w.writerow(
                [
                    s.value,
                    f"{s.confidence:.2f}",
                    c.siren,
                    c.siret_main,
                    c.name,
                    c.naf_code,
                    c.headcount_label,
                    c.department_code,
                    c.postal_code,
                    c.commune,
                    c.creation_date.isoformat(),
                    s.reason,
                ]
            )
    return path
