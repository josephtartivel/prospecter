# Langfuse integration — diff to apply

Goal: every LLM call goes through `llm.py`, and every call shows up in Langfuse with cost, latency, tokens, and a trace that groups all calls of one ICP run together.

Total work: ~10 minutes. Adds ~15 lines.

---

## 1. Add the dependency

```toml
# pyproject.toml — under [project.optional-dependencies] or main dependencies
"langfuse>=2.50.0",
```

```bash
uv sync
```

## 2. Env vars

Append to `.env.example`:

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com  # or self-hosted URL
LANGFUSE_ENABLED=true                      # toggle without removing keys
```

Sign up at https://cloud.langfuse.com (free tier: 50k observations/month — plenty for eval).

## 3. Patch `src/prospecter/llm.py`

At the top of the file, after your other imports:

```python
import os
import litellm

# --- Langfuse callback registration ---
# Done once at import time. Idempotent: safe if env vars are missing.
if os.getenv("LANGFUSE_ENABLED", "false").lower() == "true":
    litellm.success_callback = ["langfuse"]
    litellm.failure_callback = ["langfuse"]
```

Then, in your call wrapper (the function that calls `litellm.acompletion` / `litellm.completion`), accept and forward a `metadata` kwarg:

```python
async def call(
    *,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    run_id: str | None = None,        # NEW
    agent_name: str | None = None,    # NEW
    icp_id: str | None = None,        # NEW
    **kwargs,
) -> ...:
    # Group all calls of one prospecter run under a single Langfuse trace.
    # Tags let you filter the Langfuse UI by agent or ICP.
    metadata = {
        "trace_id": run_id,                      # one ICP run = one trace
        "trace_name": "prospecter-run",
        "session_id": run_id,
        "tags": [t for t in [agent_name, icp_id] if t],
        "generation_name": agent_name,           # appears as the span name
    }

    response = await litellm.acompletion(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        metadata=metadata,
        **kwargs,
    )
    # ... your existing tracking code (in_tokens, out_tokens, cost_usd, etc.)
    return response
```

## 4. Pass the IDs from the agents

In each agent (`icp_parser.py`, `scorer.py`):

```python
# Where you currently call llm.call(...)
response = await llm.call(
    model=cfg.model,
    messages=messages,
    tools=[tool_def],
    tool_choice={"type": "function", "function": {"name": tool_def["function"]["name"]}},
    run_id=state.run_id,         # already on RunState
    agent_name="icp_parser",     # or "scorer"
    icp_id=state.icp_id,         # add to RunState if missing
)
```

## 5. README addition

Add to `README.md` after the "Eval" section:

```markdown
## Observability

Every LLM call is captured by Langfuse with cost, latency, token counts,
and the full prompt/response. Calls of one ICP run share a single trace.

![langfuse trace](docs/langfuse_trace.png)
```

Take one screenshot of a trace, drop it in `docs/langfuse_trace.png`, commit.

## 6. ADR

Add `notes/0004-langfuse-observability.md`:

```markdown
# ADR 0004 — Langfuse for LLM observability

## Decision
Use Langfuse via the LiteLLM callback hook for trace, cost, and latency
visibility on every LLM call.

## Alternatives rejected
- **Custom file logging**: works but no UI, no diff between runs, no
  group-by-trace. Replaces a free tool with maintenance.
- **Helicone**: HTTP proxy approach forces all traffic through their
  endpoint. Adds a network hop and a vendor dependency on the request
  path, not the observability path.
- **Arize Phoenix (self-hosted)**: stronger eval features but heavier to
  run; overkill for a single-laptop project.

## Consequences
- One env var (`LANGFUSE_ENABLED=false`) disables it for offline / CI runs.
- Trace IDs are aligned with `RunState.run_id`, so an eval row can be
  cross-referenced to its trace in one click.
- Free tier (50k observations/month) covers ~30 ICPs × 50 candidates
  × 4 configs = 6k calls per full eval, with margin.
```

---

## Why this is a high-impact 5-line change

A recruiter who clones the repo and runs `pytest` sees a working pipeline.
A recruiter who scrolls the README and sees a Langfuse trace screenshot
understands you ship code with prod observability. The screenshot does
the talking — most juniors talk about observability and show none.
