"""Sanity tests for the LiteLLM-backed wrapper.

The Langfuse callback must be opt-in: importing `prospecter.llm` with
`LANGFUSE_ENABLED=false` (or unset) must succeed without any Langfuse
key present and must not register the callback. Hard requirement of
ADR-007 — observability is free to add and free to skip.
"""

from __future__ import annotations

import importlib

import litellm


def test_import_disabled_requires_no_langfuse_keys(monkeypatch):
    """With LANGFUSE_ENABLED=false and no keys, importing llm must not crash
    and must not register the callback on the litellm singleton."""
    litellm.success_callback = []
    litellm.failure_callback = []
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.delenv(k, raising=False)

    import prospecter.llm as llm_mod

    importlib.reload(llm_mod)  # re-runs the module-level guard under the patched env

    assert "langfuse" not in (litellm.success_callback or [])
    assert "langfuse" not in (litellm.failure_callback or [])
