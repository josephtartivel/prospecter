"""LangGraph wiring for the parse → search → score pipeline.

Three nodes, one edge each, no conditionals yet. Phase-2 work will add
edges that re-enter the parser when search returns empty/excessive
results. Skeleton — implement in session 4.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from langgraph.graph import END, StateGraph

from prospecter.agents.icp_parser import parse_icp
from prospecter.llm import LLM
from prospecter.schemas import RunState, TraceEvent
from prospecter.tools.duckdb_tool import SireneStore

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def build_graph(*, llm: LLM, store: SireneStore):
    """Return a compiled LangGraph app for one prospecter run.

    Each node mutates and returns the `RunState`. The trace is appended,
    not replaced, so the Streamlit UI can stream the diff.
    """
    g: StateGraph = StateGraph(RunState)

    def parse_node(state: RunState) -> RunState:
        t0 = _now()
        state.trace.append(TraceEvent(at=t0, agent="icp_parser", kind="start"))
        icp = parse_icp(state.nl_query, llm=llm)
        state.icp = icp
        state.trace.append(
            TraceEvent(
                at=_now(),
                agent="icp_parser",
                kind="finish",
                payload={"icp": icp.model_dump()},
                duration_ms=int((_now() - t0).total_seconds() * 1000),
            )
        )
        return state

    def search_node(state: RunState) -> RunState:
        # TODO(session-4): import and call search.search(state.icp, store=store)
        # then append candidates and a finish event.
        raise NotImplementedError("implement after Search agent — session 4")

    def score_node(state: RunState) -> RunState:
        # TODO(session-4): asyncio.run(score_candidates(state.icp, state.candidates, llm=llm))
        # append scores and a finish event; sort scores by value desc inside.
        raise NotImplementedError("implement after Scorer agent — session 4")

    g.add_node("parse", parse_node)
    g.add_node("search", search_node)
    g.add_node("score", score_node)

    g.set_entry_point("parse")
    g.add_edge("parse", "search")
    g.add_edge("search", "score")
    g.add_edge("score", END)

    return g.compile()
