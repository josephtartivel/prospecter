"""ICPParser — natural language → typed ICP via tool-use.

This module is the reference implementation for how an agent in this
project is structured: load a versioned prompt, expose one Pydantic-derived
tool, call the LLM, parse + validate, retry on validation error, return.
The other agents follow the same pattern.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from pydantic import ValidationError

from prospecter.llm import LLM
from prospecter.prompt_library import PromptLibrary
from prospecter.schemas import ICP

log = logging.getLogger(__name__)

TOOL_NAME = "submit_icp"


def _icp_tool_definition() -> dict[str, Any]:
    """OpenAI/LiteLLM-style tool definition with Pydantic-derived JSON schema."""
    schema = ICP.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "Submit the parsed ICP. Call this exactly once. "
                "Leave fields unset if the input doesn't clearly imply them."
            ),
            "parameters": schema,
        },
    }


def _extract_tool_call(response: Any) -> dict[str, Any] | None:
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
    if name != TOOL_NAME:
        return None
    args_raw = fn["arguments"] if isinstance(fn, dict) else getattr(fn, "arguments", "")
    if not args_raw:
        return None
    try:
        return json.loads(args_raw)
    except json.JSONDecodeError:
        log.warning("model returned non-JSON tool args: %r", args_raw)
        return None


def parse_icp(
    nl: str,
    *,
    llm: LLM,
    prompts: PromptLibrary | None = None,
    model: str | None = None,
    max_attempts: int = 3,
) -> ICP:
    """Parse a natural-language ICP description into a typed `ICP`.

    Retries up to `max_attempts` times on Pydantic validation errors,
    feeding the error back to the model so it can self-correct.
    """
    prompts = prompts or PromptLibrary()
    system = prompts.load("icp_parser", version=2)
    chosen_model = model or os.environ.get("PROSPECTER_MODEL_PARSER", "claude-haiku-4-5")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": nl.strip()},
    ]

    last_error: str | None = None
    for attempt in range(1, max_attempts + 1):
        response = llm.call(
            model=chosen_model,
            messages=messages,
            tools=[_icp_tool_definition()],
            tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
            max_tokens=512,
            temperature=0.0,
            cache_system_prompt=True,
            agent_name="icp_parser",
        )
        args = _extract_tool_call(response)
        if args is None:
            last_error = "model did not call submit_icp"
            log.warning("attempt %d: %s", attempt, last_error)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You did not call submit_icp. "
                        "Call the tool exactly once with the structured fields."
                    ),
                }
            )
            continue

        try:
            return ICP.model_validate(args)
        except ValidationError as e:
            last_error = str(e)
            log.warning("attempt %d validation failed: %s", attempt, last_error)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"The submitted ICP failed validation. Fix and resubmit:\n{last_error}"
                    ),
                }
            )

    raise RuntimeError(
        f"failed to parse ICP after {max_attempts} attempts; last error: {last_error}"
    )
