"""Thin LiteLLM-backed client wrapper.

Two responsibilities:

1. Single entry point for every model call in the project. The agent code
   imports `LLM` from here and never touches `litellm.completion` directly.
2. Per-call accounting: model name, token counts, dollar cost, wall-clock.
   This is what makes `$/ICP` and `p95 latency` numbers in the eval real
   instead of guessed.

We deliberately don't abstract further. LiteLLM already gives us a
provider-neutral interface; an extra layer on top would add bugs without
adding signal.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import litellm
from litellm import completion as litellm_completion
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# LiteLLM has its own retry/backoff but we centralise here so behaviour is
# identical regardless of provider. We only retry on transient errors.
_TRANSIENT = (
    litellm.exceptions.RateLimitError,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.APIError,
    litellm.exceptions.Timeout,
)


@dataclass
class CallRecord:
    """One observable model call."""

    model: str
    in_tokens: int
    out_tokens: int
    cost_usd: float
    duration_ms: int


@dataclass
class LLM:
    """Stateless-ish wrapper that records calls into `history`.

    Pass an instance into agents so usage is centrally tracked. One `LLM`
    per run; the eval harness creates a fresh one per ICP and reads
    `history` afterwards to build the cost table.
    """

    primary_model: str
    history: list[CallRecord] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> LLM:
        return cls(primary_model=os.environ.get("PROSPECTER_MODEL_PARSER", "claude-haiku-4-5"))

    @retry(
        retry=retry_if_exception_type(_TRANSIENT),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def call(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, Any]],
        tools: Iterable[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        cache_system_prompt: bool = False,
    ) -> dict[str, Any]:
        """Make one LiteLLM call and record it.

        Returns the raw LiteLLM response dict; callers are responsible for
        extracting tool calls or content. We don't pre-parse because tool
        schemas vary across agents and a pre-parser would just push the
        if/elif into here.

        `cache_system_prompt=True` adds Anthropic's `cache_control: ephemeral`
        marker to the system message. LiteLLM forwards it when the model is
        Anthropic-flavoured; it's a no-op on other providers.
        """
        chosen = model or self.primary_model
        msgs = list(messages)
        if cache_system_prompt and msgs and msgs[0].get("role") == "system":
            sys = msgs[0]
            content = sys.get("content")
            if isinstance(content, str):
                # Anthropic prompt caching syntax via LiteLLM passthrough.
                msgs[0] = {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }

        kwargs: dict[str, Any] = {
            "model": chosen,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = list(tools)
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        t0 = time.perf_counter()
        response = litellm_completion(**kwargs)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        usage = (
            response.get("usage")
            if isinstance(response, dict)
            else getattr(response, "usage", None)
        )
        in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
        out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
        # LiteLLM exposes `_hidden_params["response_cost"]` when it can compute it.
        cost = 0.0
        try:
            hp = getattr(response, "_hidden_params", None) or {}
            cost = float(hp.get("response_cost") or 0.0)
        except Exception:
            cost = 0.0

        self.history.append(
            CallRecord(
                model=chosen,
                in_tokens=in_tok,
                out_tokens=out_tok,
                cost_usd=cost,
                duration_ms=elapsed_ms,
            )
        )
        return response  # type: ignore[return-value]

    @property
    def total_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.history)

    @property
    def total_calls(self) -> int:
        return len(self.history)
