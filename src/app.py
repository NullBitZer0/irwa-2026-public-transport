"""
LankaJourney AI — Streamlit Web Interface
Member 4 — Responsible AI, Commercialization & Media Lead

Renders the chat UI, route cards, and HITL booking confirmation widgets.
Connects to the Orchestration Agent at http://localhost:8000/chat.

Start:
    streamlit run src/app.py
"""

from __future__ import annotations

import httpx
import streamlit as st

ORCHESTRATOR_URL = "http://localhost:8000"

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="LankaJourney AI",
    page_icon="🚆",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🚆 LankaJourney AI")
st.caption("Multi-Agent Public Transit Planning & Booking for Sri Lanka")


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Session")
    lang_pref = st.selectbox(
        "Language / Style",
        ["Singlish / English", "English Only", "Sinhala"],
    )
    st.markdown("---")
    st.info("🔒 **Zero Trust Active**\nNIC numbers are masked before LLM processing.")

    # HITL confirmation
    st.markdown("---")
    st.subheader("🎫 Booking Confirmation")
    hitl_approved = st.checkbox("✅ I confirm the selected route booking", value=False)
    selected_route_id = st.text_input("Route ID to book:", placeholder="TRAIN-1001")


# ── Chat state ────────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = None

# Render existing messages
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# ── Chat input ────────────────────────────────────────────────────────────────
prompt = st.chat_input(
    "E.g., Heta ude 6ta Kandy indan Galle yanna train ekak thiyeda?"
)

if prompt:
    # Display user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call Orchestration Agent
    with st.chat_message("assistant"):
        with st.spinner("Consulting transit schedules…"):
            try:
                payload = {
                    "query": prompt,
                    "session_id": st.session_state.session_id,
                    "hitl_approved": hitl_approved,
                    "selected_route_id": selected_route_id or None,
                }
                res = httpx.post(
                    f"{ORCHESTRATOR_URL}/chat",
                    json=payload,
                    timeout=15.0,
                )
                res.raise_for_status()
                data = res.json()

                # Persist session ID across turns
                st.session_state.session_id = data.get("session_id")

                reply: str = data.get("response", "No response received.")

                # Show route cards if available
                route_options: list = data.get("route_options", [])
                if route_options:
                    st.markdown("**Available Routes:**")
                    for route in route_options:
                        with st.expander(
                            f"🚄 {route.get('service_name')} — {route.get('route_id')}"
                        ):
                            st.markdown(
                                f"**{route.get('origin')}** → **{route.get('destination')}**  \n"
                                f"Departs: `{route.get('departure_time')}` | "
                                f"Arrives: `{route.get('arrival_time')}`  \n"
                                f"Fare: **LKR {route.get('base_fare_lkr', 0):.0f}**  \n"
                                f"Provider: {route.get('provider')} | Type: {route.get('transit_type')}"
                            )

                # Booking reference
                if data.get("booking_reference"):
                    st.success(f"✅ Booking Reference: **{data['booking_reference']}**")

            except httpx.ConnectError:
                reply = (
                    "⚠️ **Orchestration Agent is offline.**\n\n"
                    "Start it with:\n```\nuvicorn src.orchestrator.server:app --port 8000 --reload\n```"
                )
            except Exception as exc:
                reply = f"⚠️ Error: {exc}"

        st.markdown(reply)
        st.session_state.messages.append({"role": "assistant", "content": reply})

