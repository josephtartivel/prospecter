"""Scorer agent — (ICP, Company) → Score via tool-use, in parallel.

Skeleton — see SPEC §7 for the contract. Implement in the third build
session (PROMPTS.md, session 3).

Notes for the implementer:
- Mirror the pattern in `icp_parser.py`: one tool definition derived from
  `Score`, a single LiteLLM call per candidate, retry once on validation.
- Run candidates concurrently with `asyncio.gather` and a semaphore. The
  default cap is 8 — high enough to be useful, low enough that free-tier
  rate limits don't bite.
- The system prompt is `prompts/scorer_v1.md`. Pass `cache_system_prompt=True`
  on every call; on Anthropic this caches the rubric across calls.
- Drop a candidate (don't fail the run) if scoring repeatedly fails for it.
  Surface in the trace so the eval harness can count failures.
"""

from __future__ import annotations

import logging

from prospecter.llm import LLM
from prospecter.prompt_library import PromptLibrary
from prospecter.schemas import Company, ICP, Score

log = logging.getLogger(__name__)


async def score_candidates(
    icp: ICP,
    candidates: list[Company],
    *,
    llm: LLM,
    prompts: PromptLibrary | None = None,
    model: str | None = None,
    concurrency: int = 8,
) -> list[Score]:
    """Score every candidate in parallel; return scores in input order.

    Failed candidates are silently dropped from the result; the trace
    records which SIRENs failed.
    """
    raise NotImplementedError("implement in session 3 (see PROMPTS.md)")
