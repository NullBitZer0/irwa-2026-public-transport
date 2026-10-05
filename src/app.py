"""
LankaJourney AI — legacy Streamlit interface (superseded)

Member 4 — Responsible AI, Commercialization & Media Lead

⚠️ SUPERSEDED. The shipped interface is the React frontend in `frontend/`,
served by nginx on port 3000 (see docker-compose.yml). This file is retained
only as a reference implementation and is not started by any service.

It is kept in the tree rather than deleted because it documents the original
single-file UI, but it is no longer a live attack surface — nothing serves it.
Were it re-enabled, the escaping below is what makes it safe.

Markdown safety (finding F-04): `st.markdown()` renders a limited subset of
Markdown, but `unsafe_allow_html=True` is never passed, so raw HTML is escaped
by Streamlit. The remaining risk was untrusted text reaching the renderer at
all; `render_safe()` escapes the HTML-significant characters first, so a payload
such as `<script>` or an image tag renders as literal text.
"""

from __future__ import annotations

import html

import httpx
import streamlit as st

ORCHESTRATOR_URL = "http://localhost:8000"

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="LankaJourney AI (legacy)",
    page_icon="🚆",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🚆 LankaJourney AI — legacy interface")
st.warning(
    "This Streamlit UI is **superseded** by the React frontend on port 3000. "
    "It is kept for reference and is not part of the deployed stack."
)


def render_safe(text: str) -> str:
    """
    Escapes user- and agent-supplied text before it reaches the Markdown renderer.

    Streamlit's `st.markdown` already refuses raw HTML unless explicitly told
    otherwise, but escaping first means a payload cannot be reinterpreted if
    that default is ever changed, and it stops Markdown link/image syntax from
    being interpreted as live content.
    """
    return html.escape(str(text or ""), quote=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Session")
    lang_pref = st.selectbox(
        "Language / Style",
        ["Singlish / English", "English Only", "Sinhala"],
    )
    st.markdown("---")
    st.info("🔒 **Zero Trust Active**\nNIC numbers are masked via Zero Trust Gateway.")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(render_safe(msg["content"]))


# ── Chat input ────────────────────────────────────────────────────────────────
prompt = st.chat_input(
    "E.g., Heta ude 6ta Kandy indan Galle yanna train ekak thiyeda?"
)

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(render_safe(prompt))

    with st.chat_message("assistant"):
        with st.spinner("Consulting transit schedules…"):
            try:
                payload = {
                    "query": prompt,
                    "session_id": st.session_state.get("session_id"),
                }
                res = httpx.post(f"{ORCHESTRATOR_URL}/chat", json=payload, timeout=60)
                res.raise_for_status()
                data = res.json()
                st.session_state["session_id"] = data.get("session_id")
                reply = data.get("response", "")
            except httpx.HTTPError:
                reply = (
                    "⚠️ **Orchestration Agent is offline.**\n\n"
                    "Start it with:\n```\n"
                    "uvicorn src.orchestrator.server:app --port 8000 --reload\n```"
                )
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                reply = f"⚠️ Error: {exc}"

        st.markdown(render_safe(reply))
        st.session_state.messages.append({"role": "assistant", "content": reply})
