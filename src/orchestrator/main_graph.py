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
from src.orchestrator.router import _is_booking_ready, classify_user_intent
from src.orchestrator.schemas import BookingRequestPayload, RouteRequestPayload
from src.orchestrator.session_store import SLOTS
from src.orchestrator.state import TransitSessionState
from src.responsible_ai.grounding import citation_source_for

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

    # R-09: the gate is cleared by returning the signed token, not by setting a
    # flag. Absent a token there is nothing to clear, so the traveller is sent
    # back to the gate with a fresh one rather than straight to the booking.
    approved = bool(state.get("hitl_token"))

    # "Yes" to "are you ready to book?" carries the booking forward, choosing the
    # service that was proposed. Resolved here rather than in the UI so the
    # consent step works over the API too, and so the chosen route is recorded
    # before the gate issues a token bound to it.
    if not has_route and not approved and _is_booking_ready(state["user_query"]):
        proposed = SLOTS.proposed(state["session_id"])
        if proposed:
            chosen = proposed[0]["route_id"]
            logger.info(
                f"[{state['session_id']}] Booking readiness confirmed "
                f"-> {chosen}"
            )
            return {
                "intent": intent,
                "next_node": "hitl_checkpoint",
                "selected_route_id": chosen,
            }

    # Routing logic — order matters
    if has_route and not approved:
        next_node = "hitl_checkpoint"
    elif has_route and approved:
        next_node = "booking_agent"
    elif intent == "PLAN_ROUTE":
        next_node = "planning_agent"
    elif intent == "CONDITIONS":
        next_node = "conditions_node"
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

    # Live conditions first: the advisory sentence belongs in the reply even when
    # the traveller still has to answer a clarification question, and gathering it
    # is independent of the search.
    advisory = await _conditions_for(entities)

    # Prefer the clarification-first journey search: it reports what the
    # traveller still needs to say (a mode, a time) instead of guessing.
    try:
        journey = await _bridge.call_journey_search(
            origin=entities.get("origin", "") or "",
            destination=entities.get("destination", "") or "",
            travel_mode=entities.get("mode", "ANY"),
            time_preference=entities.get("departure_time"),
            raw_query=state["user_query"],
            avoid_modes=(advisory or {}).get("advisory", {}).get("avoid_modes") or [],
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
            "messages": [_prepend_conditions(advisory, msg)],
            "route_options": [],
            "clarification": {"missing": missing, "options": options},
            "conditions": advisory,
        }

    if services:
        # Remember the proposals: a later bare "yes" needs something to point at.
        SLOTS.propose(state["session_id"], services)
        return {
            "messages": [
                _prepend_conditions(advisory, _journey_services_message(services, jdata))
            ],
            "route_options": _with_provenance(services),
            "conditions": advisory,
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

    # The planner's own note about reordering, when conditions demoted a mode.
    conditions_note = jdata.get("conditions_note")
    note_block = f"\n\n_{conditions_note}_" if conditions_note else ""

    # Anything demoted is marked inline, so the traveller can see *why* an option
    # is listed lower rather than assuming it was filtered out.
    for i, svc in enumerate(services):
        if svc.get("conditions_flag"):
            lines[i] += f"\n   ⚠️ {svc['conditions_flag']}"

    return (
        f"Here are the {mode_word} options from **{jdata.get('origin')}** to "
        f"**{jdata.get('destination')}**{when}:"
        f"{note_block}\n\n"
        + "\n".join(lines)
        + "\n\nAre you ready to book? Reply **yes** and I'll hold the first one "
        "for you, or name a different service from the list above."
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

        return {"route_options": _with_provenance(routes + connections), "messages": [msg]}

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

async def hitl_checkpoint_node(state: TransitSessionState) -> dict:
    """
    Pauses the booking flow and asks the traveller to explicitly confirm.

    R-09: this node also asks the Booking Agent to mint a signed confirmation
    token bound to this session, route and fare, and returns it to the UI. The
    traveller's approval is that token coming back — so what is being approved is
    pinned down server-side, instead of the client asserting `hitl_approved: true`
    and the server taking it on trust.

    If the token cannot be minted the booking is refused rather than downgraded
    to the old boolean: a gate that silently disappears under failure is not a
    gate.
    """
    route_id = state.get("selected_route_id", "Unknown")
    entities: dict = state.get("extracted_entities") or {}

    fare = await _bridge.fetch_fare(route_id)
    if fare is None:
        logger.warning(
            f"[{state['session_id']}] No published fare for {route_id} — cannot gate"
        )
        return {
            "messages": [
                f"⚠️ I can't take this booking: there is no published fare for "
                f"**{route_id}`**, so I won't ask you to approve a price I don't "
                f"have. Please pick another service."
            ],
            "route_options": [],
        }

    try:
        token = await _bridge.request_hitl_token(
            session_id=state["session_id"],
            route_id=route_id,
            fare_lkr=fare,
            seat_count=int(entities.get("passengers", 1)),
        )
    except Exception as exc:
        logger.error(f"[{state['session_id']}] Could not issue a HITL token: {exc}")
        return {
            "messages": [
                "⚠️ I can't open the confirmation step right now, so I'm not going "
                "to book anything. Please try again in a moment."
            ],
            "booking_status": "ERROR",
            "error": str(exc),
        }

    msg = (
        f"⚠️ **Human-in-the-Loop Confirmation Required**\n\n"
        f"You are about to hold a seat on route **`{route_id}`** at "
        f"**LKR {fare:,.0f}**.\n\n"
        f"Please confirm by:\n"
        f"- Clicking **✅ Confirm & Hold Seat** in the sidebar, or\n"
        f"- Sending: *\"YES confirm {route_id}\"*"
    )
    logger.info(f"[{state['session_id']}] HITL checkpoint reached for route {route_id}")
    return {"messages": [msg], "hitl_token": token}


# ── Node: Booking Agent ───────────────────────────────────────────────────────

async def booking_agent_node(state: TransitSessionState) -> dict:
    """
    Delegates seat-hold execution to Member 3's Booking Agent via HTTP MCP call.

    Only reached once the traveller has returned a signed HITL token (R-09).
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

    provider = _provider_for(state, route_id)
    payload = BookingRequestPayload(
        route_id=route_id,
        provider=provider,
        passenger_token=passenger_token,
        seat_count=int(entities.get("passengers", 1)),
        fare_lkr=fare,
        session_id=state["session_id"],
        # R-09: the traveller's approval, as a capability the client returned.
        # An empty token means the gate was never cleared, and the Booking Agent
        # refuses — which is the correct outcome, not a fallback to trusting a flag.
        hitl_token=state.get("hitl_token") or "",
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


# ── Node: Live conditions ─────────────────────────────────────────────────────

async def conditions_node(state: TransitSessionState) -> dict:
    """
    Answers a weather or incident question about the journey in progress.

    Mid-conversation this is the common case: the traveller has said where they
    are going and asks whether the weather or a strike will affect them. So it
    reads the route from the session's slots rather than demanding a new one.

    Deliberately does NOT reset those slots. "How will the weather be?" is a
    question about the trip, not a new trip — clearing the origin here would make
    the traveller repeat themselves mid-flow.
    """
    entities: dict = state.get("extracted_entities") or {}
    origin = entities.get("origin") or ""
    destination = entities.get("destination") or ""

    if not origin and not destination:
        return {
            "messages": [
                "I can check the weather and any reported incidents — which "
                "journey should I look at? Tell me where you're going."
            ],
            "route_options": [],
        }

    conditions = await _conditions_for(entities)
    if conditions is None:
        return {
            "messages": [
                "I can't reach the live conditions service right now, so I "
                "can't confirm the weather or any incidents. Please check the "
                "operator's site or a weather app before you travel."
            ],
            "route_options": [],
        }

    advisory = conditions.get("advisory") or {}
    weather = advisory.get("weather") or {}
    news = advisory.get("news") or {}

    where = " → ".join(part for part in (origin, destination) if part)
    parts: list[str] = []
    if conditions.get("sentence"):
        parts.append(conditions["sentence"])

    # A little detail beyond the headline sentence, so the answer is worth the
    # round trip.
    temp = (weather.get("origin") or {}).get("temperature_c")
    if temp is not None:
        parts.append(f"Currently around {temp}°C at your departure point.")

    if news.get("available"):
        if news.get("reasons"):
            parts.append("I checked recent transit news as well.")
        else:
            count = conditions.get("news_count", 0)
            noun = "headline" if count == 1 else "headlines"
            parts.append(
                f"I scanned {count} recent transit news {noun} and found "
                f"nothing else affecting this route."
            )
    else:
        parts.append("I couldn't reach the news feed, so incidents are unchecked.")

    parts.append(f"_(about {where})_" if where else "")
    return {
        "messages": [" ".join(p for p in parts if p)],
        "route_options": [],
        "conditions": conditions,
    }


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

async def _conditions_for(entities: dict) -> dict | None:
    """
    Asks the Conditions Agent about this journey.

    Best-effort by design: if the agent is down the traveller still gets a route,
    just without the live advisory. Reporting "conditions unknown" is handled by
    the agent itself, so an outage never turns into a false "everything is fine".
    """
    try:
        response = await _bridge.fetch_conditions(
            origin=entities.get("origin") or "",
            destination=entities.get("destination") or "",
            travel_mode=entities.get("mode") or "ANY",
        )
        data = response.data or {}
        return {
            "advisory": data.get("advisory") or {},
            "sentence": data.get("sentence"),
            "news_count": data.get("news_count", 0),
        }
    except Exception as exc:
        logger.warning(f"Conditions agent unavailable: {exc}")
        return None


def _prepend_conditions(conditions: dict | None, message: str) -> str:
    """
    Puts the live advisory above the route list.

    Lead with it: if there is a strike on the line, that is what the traveller
    needs to read first, before a timetable they may not be able to use.
    """
    if not conditions:
        return message
    sentence = conditions.get("sentence")
    return f"{sentence}\n\n{message}" if sentence else message


def _provider_for(state: TransitSessionState, route_id: str) -> str:
    """
    Which operator runs the selected service.

    Read from the route the traveller actually picked, rather than assumed to be
    SLR: an SLTB ticket booked as an SLR one gets the wrong reference prefix and
    the wrong contact details on the ticket.
    """
    for route in state.get("route_options") or []:
        if route.get("route_id") != route_id:
            continue
        code = str(route.get("provider") or "").upper()
        if code in ("SLR", "SLTB", "PRIVATE_HIGHWAY", "RM"):
            return "SLTB" if code == "RM" else code
        # Fall back to the route id's operator prefix.
        prefix = route_id.split("-", 1)[0].upper()
        return "SLTB" if prefix in ("RM", "SLTB") else "SLR"
    return "SLR"


def _with_provenance(routes: list[dict]) -> list[dict]:
    """
    Attaches a data-provenance citation to each route.

    Responsible AI / transparency: a recommendation should never appear without
    the dataset it came from. Deciding this server-side keeps one copy of the
    provider→dataset mapping, and means a new UI cannot forget to show it.

    Connections carry `legs`, so they are cited per leg.
    """
    cited: list[dict] = []
    for route in routes:
        legs = route.get("legs")
        if isinstance(legs, list) and legs:
            sources = list(dict.fromkeys(citation_source_for(leg) for leg in legs))
            route = {**route, "citation_source": " + ".join(sources)}
        else:
            route = {**route, "citation_source": citation_source_for(route)}
        cited.append(route)
    return cited


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
    builder.add_node("conditions_node", conditions_node)
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
            "conditions_node": "conditions_node",
            "clarify_node": "clarify_node",
        },
    )

    # All worker nodes terminate after their task
    for node in (
        "planning_agent",
        "booking_agent",
        "hitl_checkpoint",
        "faq_node",
        "conditions_node",
        "clarify_node",
    ):
        builder.add_edge(node, END)

    return builder.compile()

