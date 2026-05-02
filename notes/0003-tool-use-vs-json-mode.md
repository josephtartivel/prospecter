# 0003 — Tool-use for structured output

**Date:** 2026-05-01 · **Status:** Accepted

## Context

Both `ICPParser` and `Scorer` need typed Pydantic outputs. There are two
mainstream ways:

1. **JSON mode** — instruct the model to return JSON, parse the string,
   validate.
2. **Tool-use** — define a tool (e.g. `submit_icp`) whose schema is the
   Pydantic model's JSON schema; the model "calls" the tool with
   arguments; we validate.

## Decision

Tool-use everywhere structured output is required. JSON mode is the
documented fallback for models that handle tool-use poorly (some
open-weight chat-tunes).

## Why

- **Schema is part of the API contract**, not embedded in the prompt.
  The model gets the schema as part of the tool definition; the prompt
  stays focused on *behaviour* (when to call which tool, what to put in
  fields).
- **Failure mode is cleaner**: invalid arguments raise a Pydantic
  `ValidationError` we can append to the conversation as a tool-result
  message. The retry protocol is one extra turn, not a parse-and-pray
  loop.
- **No JSON-string escaping issues.** No trailing-comma hell, no markdown
  fences accidentally wrapping the JSON, no `"true"` (string) instead of
  `true` (bool).
- **LiteLLM normalises tool-use across providers** — Anthropic, OpenAI,
  DeepSeek, Mistral all expose roughly the same shape via LiteLLM's
  `tools` and `tool_choice` parameters. JSON-mode normalisation is
  weaker because not all providers have a "force JSON" flag.

## Consequences

- Pydantic models in `schemas.py` are doubly-loaded: as runtime
  validators and as JSON-schema sources for tool definitions. We're
  paying that cost for the rest of the project's life — fine.
- On models without reliable tool-use, we explicitly fall back to JSON
  mode with a system-prompt schema reminder. That's documented in the
  scorer agent and exercised in eval if such a model is added.
- We forfeit one potential optimisation: JSON-mode responses are
  sometimes faster than tool-use because they skip the tool-call
  formatting step. Worth ~50–100ms per call on typical models. We accept
  the cost for the validation guarantees.
