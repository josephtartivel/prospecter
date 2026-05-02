# 0001 — LangGraph for orchestration, LiteLLM for calls

**Date:** 2026-04-30 · **Status:** Accepted

## Context

Three agents (`ICPParser` → `Search` → `Scorer`) in a fixed sequence,
plus a Streamlit UI that wants real-time visibility into which agent is
firing, plus an eval harness that should be able to resume on partial
failure.

Two questions: how do we orchestrate, and what do we use for model calls?

## Considered

**Orchestration**

1. **Hand-rolled state machine.** ~60 lines, fully under our control,
   readable in any interview. Loses streaming-friendly events and
   checkpointing — we'd build those ourselves.
2. **LangGraph `StateGraph`.** Typed Pydantic state, stream-mode events
   that map cleanly to a Streamlit chat UI, optional persistent
   checkpointing for resumable runs. Adds a dependency and one new
   mental model.
3. **CrewAI / AutoGen / similar.** More opinionated frameworks with
   role-prompting and chat-style coordination. Wrong shape for a fixed
   pipeline; too much magic.

**Model surface**

1. **anthropic SDK directly.** Cleanest tool-use semantics, supports
   prompt caching natively. Locks the project to one provider.
2. **LiteLLM.** Provider-neutral, normalises tool-use across Anthropic,
   OpenAI, DeepSeek, Mistral, vLLM. Forwards provider-specific kwargs
   (e.g. `cache_control`) when targeting the relevant model.

## Decision

**LangGraph + LiteLLM.**

LangGraph wins on three concrete benefits:
- Stream-mode events feed the Streamlit trace directly. Hand-rolling that
  would mean inventing an event protocol.
- Checkpointing makes the eval harness robust to API outages mid-run.
- Typed `RunState` keeps the contract explicit; nodes are pure-ish
  functions of state.

LiteLLM wins because the eval *requires* a multi-provider ablation. A
hand-rolled abstraction over `anthropic` + `openai` + `deepseek` SDKs
would re-implement what LiteLLM already does well.

## Consequences

- Dependencies: `langgraph` and `litellm` are core. Transitive deps grow,
  but neither pulls in surprising weight.
- We use the SDK pattern *inside* each LangGraph node — i.e. `llm.call(...)`
  not `langchain-anthropic`. This keeps the prompt-engineering surface
  honest and unobscured.
- The eval ablation across providers is ~10 lines of config-swap, not a
  rewrite per provider.
- Tradeoff: anyone reading the project needs to know two libraries
  instead of one. Documented in the README and SPEC.
