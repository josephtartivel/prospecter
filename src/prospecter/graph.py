"""LangGraph wiring for the parse → search → score pipeline.

Three nodes, plus a conditional edge after ``search`` that short-
circuits to ``END`` when the node records an error. Phase-2 work will
add edges that re-enter the parser when search returns empty/excessive
results.

All nodes are intentionally synchronous. LangGraph 1.x rejects async
nodes when invoked via the sync ``.invoke()`` entry point with a
``TypeError: No synchronous function provided``; mixing the two would
require migrating ``pipeline.run`` to ``asyncio.run(app.ainvoke(...))``.
The scorer agent is async internally — ``score_node`` bridges via
``asyncio.run`` because no event loop runs inside a sync node when the
graph is all-sync. If you ever turn one of these nodes ``async``, also
flip the call site to ``ainvoke`` or you will break the run.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from langgraph.graph import END, StateGraph

from prospecter.agents.icp_parser import parse_icp
from prospecter.agents.scorer import score_candidates
from prospecter.agents.search import search
from prospecter.llm import LLM
from prospecter.schemas import RunState, TraceEvent
from prospecter.tools.duckdb_tool import SireneStore

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def build_graph(*, llm: LLM, store: SireneStore):
    """Return a compiled LangGraph app for one prospecter run.

    Each node mutates and returns the ``RunState``. The trace is
    appended, not replaced, so the Streamlit UI can stream the diff.
    Search and score are *fail-soft*: an exception is captured into
    ``state.error`` plus a ``kind="error"`` trace event, and the
    conditional edge after search routes to ``END`` rather than letting
    the exception crash the graph.
    """
    g: StateGraph = StateGraph(RunState)

    def parse_node(state: RunState) -> RunState:
        t0 = _now()
        state.trace.append(TraceEvent(at=t0, agent="icp_parser", kind="start"))
        icp = parse_icp(state.nl_query, llm=llm)
        state.icp = icp
        t1 = _now()
        state.trace.append(
            TraceEvent(
                at=t1,
                agent="icp_parser",
                kind="finish",
                payload={"icp": icp.model_dump()},
                duration_ms=int((t1 - t0).total_seconds() * 1000),
            )
        )
        return state

    def search_node(state: RunState) -> RunState:
        t0 = _now()
        state.trace.append(TraceEvent(at=t0, agent="search", kind="start"))
        if state.icp is None:
            state.error = "search: icp is None (parser did not run)"
            state.trace.append(
                TraceEvent(
                    at=_now(),
                    agent="search",
                    kind="error",
                    payload={"error": state.error},
                )
            )
            return state
        try:
            candidates = search(state.icp, store=store)
        except Exception as e:
            log.exception("search failed")
            state.error = f"search: {e}"
            t1 = _now()
            state.trace.append(
                TraceEvent(
                    at=t1,
                    agent="search",
                    kind="error",
                    payload={"error": str(e)},
                    duration_ms=int((t1 - t0).total_seconds() * 1000),
                )
            )
            return state
        state.candidates = candidates
        t1 = _now()
        state.trace.append(
            TraceEvent(
                at=t1,
                agent="search",
                kind="finish",
                payload={"count": len(candidates)},
                duration_ms=int((t1 - t0).total_seconds() * 1000),
            )
        )
        return state

    def score_node(state: RunState) -> RunState:
        t0 = _now()
        state.trace.append(TraceEvent(at=t0, agent="scorer", kind="start"))
        if state.icp is None:
            state.error = "scorer: icp is None"
            state.trace.append(
                TraceEvent(
                    at=_now(),
                    agent="scorer",
                    kind="error",
                    payload={"error": state.error},
                )
            )
            return state
        try:
            scores = asyncio.run(
                score_candidates(
                    state.icp,
                    state.candidates,
                    llm=llm,
                    trace=state.trace,
                )
            )
        except Exception as e:
            log.exception("scoring failed")
            state.error = f"scorer: {e}"
            t1 = _now()
            state.trace.append(
                TraceEvent(
                    at=t1,
                    agent="scorer",
                    kind="error",
                    payload={"error": str(e)},
                    duration_ms=int((t1 - t0).total_seconds() * 1000),
                )
            )
            return state
        # gather already preserved input order, so sorting by value alone
        # gives a stable desc ordering with ties broken by candidate index.
        scores.sort(key=lambda s: s.value, reverse=True)
        state.scores = scores
        t1 = _now()
        state.trace.append(
            TraceEvent(
                at=t1,
                agent="scorer",
                kind="finish",
                payload={"scored": len(scores), "candidates": len(state.candidates)},
                duration_ms=int((t1 - t0).total_seconds() * 1000),
            )
        )
        return state

    def _after_search(state: RunState) -> str:
        return END if state.error else "score"

    g.add_node("parse", parse_node)
    g.add_node("search", search_node)
    g.add_node("score", score_node)

    g.set_entry_point("parse")
    g.add_edge("parse", "search")
    g.add_conditional_edges("search", _after_search, {"score": "score", END: END})
    g.add_edge("score", END)

    return g.compile()
