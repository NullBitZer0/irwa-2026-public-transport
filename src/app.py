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

from src.responsible_ai.grounding import attach_hallucination_warning, format_grounded_response

ORCHESTRATOR_URL = "http://localhost:8000"

# ── Page config (must be first Streamlit call) ─────────────────────────────────
st.set_page_config(
    page_title="LankaJourney AI",
    page_icon="🚆",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _citation_source_for(route: dict) -> str:
    """Pick citation source based on provider/type for Responsible AI provenance."""
    provider = (route.get("provider") or "").upper()
    transit_type = (route.get("transit_type") or "").upper()
    if provider == "SLR" or "TRAIN" in transit_type:
        return "Sri Lanka Railways Official Timetable (data/processed/train_schedules.json)"
    if provider in {"SLTB", "PRIVATE_HIGHWAY"} or "BUS" in transit_type:
        return "SLTB / NTC Expressway Routes (data/processed/bus_routes.json)"
    return "Sri Lanka Railways & SLTB Timetables (data/processed/)"


def render_booking_confirmation(route_id: str, fare: float, provider: str) -> None:
    """HITL confirmation widget - must be explicitly approved before booking."""
    st.warning("⚠️ Human-in-the-Loop Confirmation Required")
    st.write(f"Do you authorize holding **1 seat** on **{route_id}** ({provider}) for **LKR {fare:.2f}**?")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Confirm & Hold Seat", key=f"confirm_{route_id}", use_container_width=True):
            st.success("Seat hold initiated! You have 10 minutes to complete payment.")
            st.info("⏳ Hold expires in **10:00** — complete payment before timeout.")
    with col2:
        if st.button("❌ Cancel", key=f"cancel_{route_id}", use_container_width=True):
            st.info("Reservation cancelled. Returning to journey search.")

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

    # HITL confirmation (Responsible AI - Financial Guardrail)
    st.markdown("---")
    st.subheader("🎫 Booking Confirmation")
    st.caption("Financial actions require explicit human approval (HITL).")
    hitl_approved = st.checkbox("✅ I confirm the selected route booking", value=False)
    selected_route_id = st.text_input("Route ID to book:", placeholder="TRAIN-1001")
    # Quick inline HITL preview when a route is typed
    if selected_route_id:
        # Show confirmation widget inline for demo/viva
        st.markdown("---")
        # Fare/provider will be updated after route fetch; show placeholder here
        render_booking_confirmation(selected_route_id, 0.0, "SLR/SLTB")


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

                # Show grounded route cards if available (Responsible AI - Grounding)
                route_options: list = data.get("route_options", [])
                if route_options:
                    st.markdown("**Available Routes (Grounded):**")
                    for route in route_options:
                        citation = _citation_source_for(route)
                        grounded_md = format_grounded_response(route, citation_source=citation)
                        with st.expander(
                            f"🚄 {route.get('service_name', 'Transit')} — {route.get('route_id')} | LKR {route.get('base_fare_lkr', 0):.0f}"
                        ):
                            st.markdown(grounded_md)
                            # HITL per-route confirmation (project.txt:1232)
                            render_booking_confirmation(
                                route.get("route_id", "UNKNOWN"),
                                float(route.get("base_fare_lkr", 0.0) or route.get("fare_lkr", 0.0)),
                                route.get("provider", "Unknown"),
                            )
                    # Grounding footer for viva
                    st.caption(
                        "🔍 Every itinerary above is grounded in static timetable JSON "
                        "with RRF confidence — no generation from LLM weights."
                    )
                else:
                    # Attach hallucination warning when no routes grounded
                    reply = attach_hallucination_warning(reply, has_routes=False)

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

