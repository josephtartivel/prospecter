"""Streamlit demo with streamed agent trace.

Skeleton — implement in session 5. The flow:

  1. text_input for the NL ICP
  2. on submit: build the graph, call `app.stream(...)`, render each
     event into a chat-style transcript so the user sees agents fire in
     real time.
  3. once finished, render a dataframe of the ranked leads.

Use `langgraph`'s `stream(initial_state, stream_mode="updates")` and
match on the node name.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="prospecter", layout="wide")
st.title("prospecter")
st.caption(
    "Multi-agent B2B prospector over the SIRENE registry. "
    "Type an ICP and watch three agents fire."
)

nl = st.text_input(
    "Describe your ICP",
    value="mid-size French restaurants in Paris, 10–49 employees, opened in the last 5 years",
)

col_run, col_top = st.columns([1, 4])
with col_run:
    go = st.button("Run")
with col_top:
    top_n = st.slider("Top-N", min_value=10, max_value=100, value=50, step=10)

if go:
    st.warning(
        "UI not implemented yet — see app/streamlit_app.py and PROMPTS.md "
        "(session 5)."
    )
    # TODO(session-5):
    #  app = build_graph(llm=LLM.from_env(), store=SireneStore())
    #  with st.status("Running pipeline...", expanded=True) as status:
    #      for event in app.stream(initial_state, stream_mode="updates"):
    #          render_event(event)
    #      status.update(state="complete")
    #  st.dataframe(leads_to_dataframe(leads))
