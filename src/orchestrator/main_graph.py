"""
Orchestration State Graph
Member 1 — Orchestration Agent

Assembles and compiles the LangGraph StateGraph that coordinates all agents:

  supervisor  →  planning_agent   (PLAN_ROUTE intent)
              →  hitl_checkpoint  (route selected, awaiting HITL confirmation)
              →  booking_agent    (route selected + HITL approved)
              →  faq_node         (FAQ intent)
              →  clarify_node     (CLARIFY / unknown intent)

Each non-supervisor node writes its output to `messages` and terminates (END).
"""

from langgraph.graph import END, StateGraph

from src.orchestrator.agent_connectors import AgentDispatchBridge
from src.orchestrator.logger import get_logger
from src.orchestrator.router import classify_user_intent
from src.orchestrator.schemas import BookingRequestPayload, RouteRequestPayload
from src.orchestrator.state import TransitSessionState

logger = get_logger(__name__)

# Shared bridge instance — stateless, safe to reuse across graph invocations
_bridge = AgentDispatchBridge()


# ── Node: Supervisor ──────────────────────────────────────────────────────────

def supervisor_node(state: TransitSessionState) -> dict:
    """
    Classifies intent and determines the next graph node.

    Zero Trust: this node has no direct access to external APIs.
    It only classifies intent and sets `next_node`.
    """
    has_route = bool(state.get("selected_route_id"))
    # Slots gathered in earlier turns matter here: a reply like "at 8am by train"
    # answers a question about a journey that is already under way, and judging
    # it on this message alone would restart the conversation.
    intent = classify_user_intent(
        state["user_query"],
        has_selected_route=has_route,
        session_slots=state.get("extracted_entities"),
    )

    # Routing logic — order matters
    if has_route and not state.get("hitl_approved"):
        next_node = "hitl_checkpoint"
    elif has_route and state.get("hitl_approved"):
        next_node = "booking_agent"
    elif intent == "PLAN_ROUTE":
        next_node = "planning_agent"
    elif intent == "FAQ":
        next_node = "faq_node"
    else:
        next_node = "clarify_node"

    logger.info(f"[{state['session_id']}] Supervisor → {next_node} (intent={intent})")
    return {"intent": intent, "next_node": next_node}


# ── Node: Planning Agent ──────────────────────────────────────────────────────

async def planning_agent_node(state: TransitSessionState) -> dict:
    """
    Delegates route discovery to Member 2's Planning Agent.

    Asks for what it is missing first, then shows services for the requested
    mode and time. Falls back to keyword retrieval if the planner is unreachable.
    """
    entities: dict = state.get("extracted_entities") or {}

    # Prefer the clarification-first journey search: it reports what the
    # traveller still needs to say (a mode, a time) instead of guessing.
    try:
        journey = await _bridge.call_journey_search(
            origin=entities.get("origin", "") or "",
            destination=entities.get("destination", "") or "",
            travel_mode=entities.get("mode", "ANY"),
            time_preference=entities.get("departure_time"),
            raw_query=state["user_query"],
        )
        jdata = journey.data or {}
    except Exception as exc:
        logger.warning(f"[{state['session_id']}] Journey search unavailable: {exc}")
        return await _keyword_route_search(state, entities)

    missing = jdata.get("missing") or []
    services = jdata.get("services") or []

    if missing:
        msg, options = _journey_clarification_message(missing, jdata)
        return {
            "messages": [msg],
            "route_options": [],
            "clarification": {"missing": missing, "options": options},
        }

    if services:
        return {
            "messages": [_journey_services_message(services, jdata)],
            "route_options": services,
        }

    msg = (
        f"No {('train' if jdata.get('mode') == 'TRAIN' else 'bus')} service found from "
        f"**{jdata.get('origin')}** to **{jdata.get('destination')}**. "
        f"Try a different time, or the other mode."
    )
    return {"messages": [msg], "route_options": []}


def _journey_clarification_message(missing: list[str], jdata: dict) -> tuple[str, list[dict]]:
    """
    Builds the "what do I still need to know?" reply and its quick replies.

    Asking is better than guessing: a confident list of the wrong mode or the
    wrong hour is less useful than one short question.

    It also echoes the slots already filled, so the traveller can see the
    assistant is keeping track rather than starting over — and so a mis-parsed
    city name is visible ("did you say Galle?") instead of silently producing
    results for the wrong place.
    """
    questions = []
    if "mode" in missing:
        questions.append("train or bus?")
    if "time" in missing:
        questions.append("what time do you want to travel?")
    if "origin" in missing:
        questions.append("where are you starting from?")
    if "destination" in missing:
        questions.append("where are you heading to?")
    if "major_cities" in missing:
        questions.append(
            "I can only plan between major cities right now — which city are you going to?"
        )

    known = []
    if jdata.get("origin"):
        known.append(f"from **{jdata['origin']}**")
    if jdata.get("destination"):
        known.append(f"to **{jdata['destination']}**")
    if jdata.get("at_time"):
        known.append(f"at **{jdata['at_time']}**")

    preamble = f"Got it — you want to travel {' '.join(known)}. Still need: " if known else ""

    msg = (
        f"{preamble}**{' and '.join(questions)}**\n\n"
        f'For example: *"I need to go from Negombo to Colombo at 10am by bus"*'
    )
    options: list[dict] = []
    if "mode" in missing:
        options = [
            {"label": "🚆 Train", "value": "train"},
            {"label": "🚌 Bus", "value": "bus"},
        ]
    return msg, options


def _journey_services_message(services: list[dict], jdata: dict) -> str:
    """Renders the services that board at the requested place and time."""
    lines = []
    for i, svc in enumerate(services, 1):
        code = svc.get("route_number") or svc.get("route_id")
        passing = svc["board_type"] == "passing"
        detail = f"starts at **{svc['service_origin']}**" if passing else "direct service"
        later = " _(next one — nothing that close to your time)_" if svc.get(
            "after_requested"
        ) else ""
        lines.append(
            f"{i}. {'🚆' if svc.get('provider') == 'SLR' else '🚌'} "
            f"**{svc.get('service_name')}** (`{code}`) boards **{svc['boards_at']}** "
            f"— {detail}{later}\n"
            f"   {svc.get('service_origin')} → {svc.get('service_destination')}"
        )

    mode_word = "train" if jdata.get("mode") == "TRAIN" else "bus"
    when = f" around **{jdata['at_time']}**" if jdata.get("at_time") else ""
    return (
        f"Here are the {mode_word} options from **{jdata.get('origin')}** to "
        f"**{jdata.get('destination')}**{when}:\n\n"
        + "\n".join(lines)
        + "\n\nTell me which one and I'll check seats and hold it for you."
    )


async def _keyword_route_search(state: TransitSessionState, entities: dict) -> dict:
    """
    Fallback used only when the Planning Agent's journey endpoint is unreachable.

    Returns route cards and connections the legacy way, so the UI still has
    something to show.
    """
    payload = RouteRequestPayload(
        origin=entities.get("origin", ""),
        destination=entities.get("destination", ""),
        travel_mode=entities.get("mode", "ANY"),
        date_str=entities.get("departure_date", "TODAY"),
        time_preference=entities.get("departure_time"),
        raw_query=state["user_query"],
    )

    try:
        response = await _bridge.call_planning_agent(payload)
        data = response.data or {}
        routes: list = (data.get("route_options") or [])
        connections: list = (data.get("connections") or [])
        direction_note: str | None = data.get("direction_note")
        mixed_fallback: bool = bool(data.get("mixed_mode_fallback"))

        if routes:
            lines = []
            for i, r in enumerate(routes, 1):
                lines.append(
                    f"{i}. **{r.get('service_name', r.get('route_id'))}** "
                    f"(`{r.get('route_id')}`)  "
                    f"{r.get('origin')} → {r.get('destination')} | "
                    f"Departs {r.get('departure_time')} | "
                    f"LKR {r.get('base_fare_lkr', '?'):.0f}"
                )
            summary = "\n".join(lines)
            msg = (
                f"Found **{len(routes)}** route(s) for your journey:\n\n{summary}\n\n"
                f"To book a seat, tell me: *\"Book route TRAIN-XXXX\"* or "
                f"click **Confirm & Hold Seat** in the UI."
            )
        elif connections:
            lines = []
            for i, c in enumerate(connections, 1):
                legs = c.get("legs") or []
                leg_bits = [
                    f"{'🚆' if leg.get('mode') == 'TRAIN' else '🚌'} "
                    f"**{leg.get('service_name')}** (`{leg.get('route_id')}`) "
                    f"{leg.get('origin')} → {leg.get('destination')} "
                    f"{leg.get('departure_time')}–{leg.get('arrival_time')}"
                    + (" *(+1 day)*" if leg.get("next_day") else "")
                    for leg in legs
                ]
                mixed = "train + bus" if c.get("mixed_mode") else "same mode"
                lines.append(
                    f"{i}. **{c.get('origin')} → {c.get('destination')}** "
                    f"via **{c.get('transfer_station')}** "
                    f"({c.get('transfer_minutes')} min change, {mixed})\n"
                    + "\n".join(f"   - {bit}" for bit in leg_bits)
                    + f"\n   Total: LKR {c.get('base_fare_lkr', 0):.0f}"
                )
            header = "No direct service runs this route. Here are connecting options:\n\n"
            if mixed_fallback:
                header = (
                    "No direct service runs this route, and nothing connects in a "
                    "single mode. Here is a connecting option mixing train and bus:\n\n"
                )
            msg = (
                f"{header}"
                + "\n\n".join(lines)
                + "\n\n🔁 Connections need **two tickets**, one per leg — book each "
                "leg separately at the station or operator."
            )
        else:
            msg = (
                "No routes found for your query. Please check the origin/destination "
                "names or try a different date."
            )
            if direction_note:
                msg = f"ℹ️ {direction_note}\n\nPlease confirm the direction you want to travel."

        return {"route_options": routes + connections, "messages": [msg]}

    except Exception as exc:
        logger.warning(f"[{state['session_id']}] Planning Agent unreachable: {exc}")
        return {
            "route_options": [],
            "messages": [
                f"⚠️ The Planning Agent service is currently offline.\n"
                f"Please start it with: `uvicorn src.planner.server:app --port 8001 --reload`\n"
                f"Error: `{exc}`"
            ],
            "error": str(exc),
        }


# ── Node: HITL Checkpoint ─────────────────────────────────────────────────────

def hitl_checkpoint_node(state: TransitSessionState) -> dict:
    """
    Pauses the booking flow and asks the user to explicitly confirm.

    The UI must re-submit the query with `hitl_approved=True` to proceed.
    """
    route_id = state.get("selected_route_id", "Unknown")
    msg = (
        f"⚠️ **Human-in-the-Loop Confirmation Required**\n\n"
        f"You are about to hold a seat on route **`{route_id}`**.\n\n"
        f"Please confirm by:\n"
        f"- Clicking **✅ Confirm & Hold Seat** in the sidebar, or\n"
        f"- Sending: *\"YES confirm {route_id}\"*"
    )
    logger.info(f"[{state['session_id']}] HITL checkpoint reached for route {route_id}")
    return {"messages": [msg]}


# ── Node: Booking Agent ───────────────────────────────────────────────────────

async def booking_agent_node(state: TransitSessionState) -> dict:
    """
    Delegates seat-hold execution to Member 3's Booking Agent via HTTP MCP call.

    Only reached after HITL gate is cleared (hitl_approved=True).
    """
    route_id = state.get("selected_route_id", "")
    entities: dict = state.get("extracted_entities") or {}
    passenger_token = entities.get("passenger_token", f"GUEST-{state['session_id'][:8]}")

    # The fare is NEVER taken from the client. The Planner is the pricing
    # authority, so the price is quoted here and cannot be tampered with by
    # posting a different amount to /chat.
    fare = await _bridge.fetch_fare(route_id)
    if fare is None:
        logger.warning(f"[{state['session_id']}] No published fare for {route_id}")
        return {
            "messages": [
                f"⚠️ I can't take this booking: there is no published fare for "
                f"**{route_id}**, so I won't guess a price. Please pick another "
                f"service, or check the operator's site."
            ],
            "route_options": [],
        }

    payload = BookingRequestPayload(
        route_id=route_id,
        provider="SLR",  # TODO: derive from selected route_options data
        passenger_token=passenger_token,
        seat_count=int(entities.get("passengers", 1)),
        fare_lkr=fare,
        user_confirmed=True,  # HITL already cleared by supervisor routing
    )

    try:
        # begin_booking holds the seat and clears the HITL gate but does NOT
        # charge: the traveller pays through the payment portal afterwards.
        response = await _bridge.begin_booking(payload)
        data = response.data or {}
        txn = data.get("transaction") or {}

        ref = txn.get("transaction_id") or data.get("booking_reference") or "N/A"
        amount = txn.get("fare_lkr")
        seats = txn.get("seat_count")
        # A missing fare must not crash the formatter or read as free.
        amount_text = f"LKR {amount:,.0f}" if isinstance(amount, (int, float)) else "not available"
        msg = (
            f"✅ **Seat held!**\n\n"
            f"**Transaction:** `{ref}`\n"
            f"**Seats:** {seats if seats is not None else '—'}\n"
            f"**Amount due:** {amount_text}\n\n"
            f"⏱️ You have **10 minutes** to complete payment before the hold expires.\n\n"
            f"💳 Continue to the secure payment portal to pay and get your e-ticket."
        )
        return {
            "messages": [msg],
            "booking_reference": ref,
            "booking_status": "AWAITING_PAYMENT",
            "transaction_id": ref,
            "amount_lkr": amount,
            "seat_count": seats,
        }

    except Exception as exc:
        logger.warning(f"[{state['session_id']}] Booking Agent unreachable: {exc}")
        return {
            "messages": [
                f"⚠️ The Booking Agent service is currently offline.\n"
                f"Please start it with: `uvicorn src.booking.server:app --port 8002 --reload`\n"
                f"Error: `{exc}`"
            ],
            "booking_status": "ERROR",
            "error": str(exc),
        }


# ── Node: FAQ ─────────────────────────────────────────────────────────────────

def faq_node(state: TransitSessionState) -> dict:  # noqa: ARG001
    """Returns general transit FAQ guidance and links to official sources."""
    msg = (
        "Here are some useful resources for Sri Lanka transit:\n\n"
        "- **Sri Lanka Railways:** https://www.railway.gov.lk (timetables, reservations)\n"
        "- **SLTB eSeat:** https://www.sltb.eseat.lk (bus reservations)\n"
        "- **NTC:** https://www.ntc.gov.lk (fares, passenger rights)\n\n"
        "Feel free to ask me specific questions about routes, baggage policies, "
        "refund rules, or station information!"
    )
    return {"messages": [msg]}


# ── Node: Clarify ─────────────────────────────────────────────────────────────

def clarify_node(state: TransitSessionState) -> dict:  # noqa: ARG001
    """Prompts the user for more specific input."""
    msg = (
        "I didn't quite understand that. Here are some things I can help with:\n\n"
        "🔍 **Find a route:** *\"Find a train from Colombo Fort to Kandy tomorrow morning\"*\n"
        "🎫 **Book a seat:** *\"Book route TRAIN-1001 for 1 passenger\"*\n"
        "ℹ️ **Transit info:** *\"What are the baggage rules on SLR?\"*\n\n"
        "You can also type in Singlish — e.g., *\"Heta ude 6ta Kandy yanna train ekak thiyeda?\"*"
    )
    return {"messages": [msg]}


# ── Edge router ───────────────────────────────────────────────────────────────

def _route_from_supervisor(state: TransitSessionState) -> str:
    """Reads `next_node` set by supervisor_node for conditional edge routing."""
    return state.get("next_node", "clarify_node")


# ── Graph factory ─────────────────────────────────────────────────────────────

def build_graph():
    """
    Assembles and compiles the LangGraph orchestration state graph.

    Returns a compiled CompiledStateGraph ready for `ainvoke`.
    """
    builder: StateGraph = StateGraph(TransitSessionState)

    # Register nodes
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("planning_agent", planning_agent_node)
    builder.add_node("booking_agent", booking_agent_node)
    builder.add_node("hitl_checkpoint", hitl_checkpoint_node)
    builder.add_node("faq_node", faq_node)
    builder.add_node("clarify_node", clarify_node)

    # Entry point
    builder.set_entry_point("supervisor")

    # Conditional edges from supervisor
    builder.add_conditional_edges(
        "supervisor",
        _route_from_supervisor,
        {
            "planning_agent": "planning_agent",
            "booking_agent": "booking_agent",
            "hitl_checkpoint": "hitl_checkpoint",
            "faq_node": "faq_node",
            "clarify_node": "clarify_node",
        },
    )

    # All worker nodes terminate after their task
    for node in ("planning_agent", "booking_agent", "hitl_checkpoint", "faq_node", "clarify_node"):
        builder.add_edge(node, END)

    return builder.compile()

