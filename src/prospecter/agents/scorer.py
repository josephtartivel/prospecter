"""Scorer agent — (ICP, Company) → Score via tool-use, in parallel.

Mirrors `icp_parser.py`: one tool definition derived from `Score`,
one LiteLLM call per candidate, one retry on validation error.
Candidates run concurrently via `asyncio.gather` with a `Semaphore`
cap; results come back in input order. A failed candidate is dropped
and recorded as a trace event so the eval harness can count failures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from prospecter.llm import LLM
from prospecter.prompt_library import PromptLibrary
from prospecter.schemas import ICP, Company, Score, TraceEvent

log = logging.getLogger(__name__)

TOOL_NAME = "submit_score"


def _score_tool_definition() -> dict[str, Any]:
    """OpenAI/LiteLLM-style tool definition with Pydantic-derived JSON schema."""
    schema = Score.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "Submit the score for the candidate. "
                "Call this exactly once with the four required fields."
            ),
            "parameters": schema,
        },
    }


# Intentional duplication of `icp_parser._extract_tool_call`: the parser
# version hard-codes the tool name. DRY-up planned at the third agent
# per rule of three.
def _extract_tool_call(response: Any, tool_name: str) -> dict[str, Any] | None:
    """Pull the first matching tool-call's arguments out of a LiteLLM response."""
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        message = getattr(getattr(response, "choices", [None])[0], "message", None)
    if message is None:
        return None
    tool_calls = (
        message.get("tool_calls")
        if isinstance(message, dict)
        else getattr(message, "tool_calls", None)
    )
    if not tool_calls:
        return None
    first = tool_calls[0]
    fn = first["function"] if isinstance(first, dict) else getattr(first, "function", None)
    if fn is None:
        return None
    name = fn["name"] if isinstance(fn, dict) else getattr(fn, "name", "")
    if name != tool_name:
        return None
    args_raw = fn["arguments"] if isinstance(fn, dict) else getattr(fn, "arguments", "")
    if not args_raw:
        return None
    try:
        return json.loads(args_raw)
    except json.JSONDecodeError:
        log.warning("model returned non-JSON tool args: %r", args_raw)
        return None


def _build_user_payload(icp: ICP, company: Company) -> str:
    """Compact JSON of (ICP, Company) for the user message."""
    return json.dumps(
        {
            "icp": icp.model_dump(mode="json", exclude_none=True),
            "company": company.model_dump(mode="json"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def _score_one(
    icp: ICP,
    company: Company,
    *,
    llm: LLM,
    system: str,
    model: str,
    trace: list[TraceEvent] | None,
    max_attempts: int = 2,
) -> Score | None:
    """Score one candidate. Returns None after `max_attempts` failures."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": _build_user_payload(icp, company)},
    ]

    last_error: str | None = None
    for attempt in range(1, max_attempts + 1):
        # `LLM.call` is sync (tenacity-wrapped litellm.completion). We hop
        # to a worker thread so `asyncio.gather` over candidates is actually
        # concurrent — without `to_thread`, every call would block the loop.
        response = await asyncio.to_thread(
            llm.call,
            model=model,
            messages=messages,
            tools=[_score_tool_definition()],
            tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
            max_tokens=256,
            temperature=0.0,
            cache_system_prompt=True,
            agent_name="scorer",
        )
        args = _extract_tool_call(response, TOOL_NAME)
        if args is None:
            last_error = "model did not call submit_score"
            log.warning("siren=%s attempt %d: %s", company.siren, attempt, last_error)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You did not call submit_score. "
                        "Call the tool exactly once with the structured fields."
                    ),
                }
            )
            continue

        try:
            score = Score.model_validate(args)
        except ValidationError as e:
            last_error = str(e)
            log.warning(
                "siren=%s attempt %d validation failed: %s",
                company.siren,
                attempt,
                last_error,
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"The submitted score failed validation. Fix and resubmit:\n{last_error}"
                    ),
                }
            )
            continue

        # The candidate's SIREN is authoritative — the model occasionally
        # echoes the SIREN with a typo. Force-pin to the input row.
        if score.siren != company.siren:
            score = score.model_copy(update={"siren": company.siren})
        return score

    log.warning(
        "siren=%s scoring failed after %d attempts; dropping",
        company.siren,
        max_attempts,
    )
    if trace is not None:
        trace.append(
            TraceEvent(
                at=datetime.now(UTC),
                agent="scorer",
                kind="error",
                payload={
                    "siren": company.siren,
                    "reason": "scoring failed",
                    "error": last_error or "",
                },
            )
        )
    return None


async def score_candidates(
    icp: ICP,
    candidates: list[Company],
    *,
    llm: LLM,
    prompts: PromptLibrary | None = None,
    model: str | None = None,
    concurrency: int = 8,
    trace: list[TraceEvent] | None = None,
) -> list[Score]:
    """Score every candidate in parallel; return scores in input order.

    Failed candidates are dropped from the result. If `trace` is provided,
    one error event per failure is appended so the eval harness can count
    drops without a separate channel.
    """
    if not candidates:
        return []

    prompts = prompts or PromptLibrary()
    system = prompts.load("scorer", version=2)
    chosen_model = model or os.environ.get("PROSPECTER_MODEL_SCORER", "claude-haiku-4-5")
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(c: Company) -> Score | None:
        async with sem:
            return await _score_one(
                icp,
                c,
                llm=llm,
                system=system,
                model=chosen_model,
                trace=trace,
            )

    # `gather` preserves submission order regardless of completion order,
    # so filtering Nones still produces a stable, input-aligned slice.
    results = await asyncio.gather(*(_bounded(c) for c in candidates))
    return [s for s in results if s is not None]
