"""
Prospecter as an MCP server.

Exposes the prospecter pipeline as a tool that any MCP-compatible client
(Claude Desktop, Cursor, Cline, etc.) can call directly.

Usage from Claude Desktop:

    1. Add to ~/Library/Application Support/Claude/claude_desktop_config.json:

       {
         "mcpServers": {
           "prospecter": {
             "command": "uv",
             "args": ["run", "python", "-m", "prospecter.mcp_server"],
             "cwd": "/absolute/path/to/prospecter",
             "env": {
               "ANTHROPIC_API_KEY": "sk-ant-..."
             }
           }
         }
       }

    2. Restart Claude Desktop.
    3. Ask Claude: "Find me 20 mid-size restaurants in Paris that opened
       in the last 5 years." Claude will call the prospecter tool.

The point of this file: demonstrate that the prospecter pipeline can be
consumed by any agent runtime, not just a Streamlit app or a CLI. MCP is
becoming the lingua franca for tool-use in 2026; exposing your pipeline
as one is a 50-line investment for a meaningful signal.

Drop this file at: src/prospecter/mcp_server.py

Run standalone for testing: `uv run python -m prospecter.mcp_server`
"""

from __future__ import annotations

import logging
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from prospecter.pipeline import run as run_pipeline
from prospecter.schemas import Lead

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="prospecter",
    instructions=(
        "Prospecter generates ranked lists of French B2B prospects from "
        "natural-language Ideal Customer Profile (ICP) descriptions. "
        "It searches the official SIRENE registry, scores each candidate "
        "on NAF code, headcount, geography and age, and returns a CSV-"
        "shaped list with a one-line reason per row. Use it whenever the "
        "user wants to find or qualify French companies."
    ),
)


def _format_leads_as_markdown(leads: list[Lead], top_n: int) -> str:
    """Render leads as a markdown table for the calling LLM to read."""
    header = (
        "| Rank | SIREN | Name | NAF | City | Headcount | Score | Reason |\n"
        "|------|-------|------|-----|------|-----------|-------|--------|"
    )
    rows = []
    for i, lead in enumerate(leads[:top_n], start=1):
        c = lead.company
        rows.append(
            f"| {i} | {c.siren} | {c.name} | {c.naf} | {c.city or '-'} | "
            f"{c.headcount_tranche or '-'} | {lead.score.value}/5 | "
            f"{lead.score.reason} |"
        )
    return header + "\n" + "\n".join(rows)


@mcp.tool()
async def prospect(
    icp: Annotated[
        str,
        Field(
            description=(
                "One-sentence Ideal Customer Profile in natural language. "
                "Examples: 'mid-size restaurants in Paris with 10-49 employees, "
                "opened in the last 5 years', 'B2B SaaS companies in Lyon "
                "with 50-200 employees in the last 3 years'."
            ),
        ),
    ],
    top_n: Annotated[
        int,
        Field(
            default=20,
            ge=1,
            le=50,
            description="How many top candidates to return. Capped at 50.",
        ),
    ] = 20,
    model: Annotated[
        str,
        Field(
            default="claude-haiku-4-5",
            description=(
                "LLM used by the scorer. Defaults to Haiku (cheapest tier). "
                "Use 'claude-sonnet-4-5' for higher accuracy."
            ),
        ),
    ] = "claude-haiku-4-5",
) -> str:
    """
    Find and rank French B2B companies matching an ICP description.

    Returns a markdown table with up to `top_n` candidates, each with a
    score (1-5) and a one-line reason.
    """
    logger.info("MCP tool called: prospect(top_n=%s, model=%s)", top_n, model)

    try:
        leads = await run_pipeline(icp_text=icp, model=model, top_n=top_n)
    except Exception as exc:
        # MCP best practice: never crash the server on tool errors; return
        # the error as the tool result so the calling LLM can handle it.
        logger.exception("prospect failed")
        return f"Prospecter failed: {type(exc).__name__}: {exc}"

    if not leads:
        return "No candidates matched this ICP. Try widening the headcount range or removing geographic filters."

    return _format_leads_as_markdown(leads, top_n=top_n)


@mcp.resource("prospecter://stats")
def stats() -> str:
    """Quick stats about the indexed SIRENE dataset (snapshot date, row count)."""
    # Replace with a real read from your SireneStore once wired.
    return (
        "SIRENE snapshot date: 2026-04-15\n"
        "Active legal entities: ~5.4M\n"
        "Coverage: France (mainland + DOM-TOM)\n"
        "Source: data.gouv.fr (open data)"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run()


if __name__ == "__main__":
    main()
