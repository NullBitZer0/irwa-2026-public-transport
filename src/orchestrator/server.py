"""
Orchestration Agent FastAPI Server
Member 1 — System Architect & Orchestrator Lead

Exposes the /chat endpoint consumed by the React frontend (frontend/, :3000).
The Orchestrator runs on port 8000 by default.

Start:
    uvicorn src.orchestrator.server:app --port 8000 --reload
"""

import os
import time
import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel

load_dotenv()  # Load .env before importing modules that read env vars

from src.orchestrator.accounts import (  # noqa: E402
    SESSION_COOKIE,
    AccountStore,
    hash_password,
    normalise_contact,
    public_profile,
    summarise_card,
    verify_password,
)
from src.orchestrator.conversations import (  # noqa: E402
    CONVERSATIONS,
    ConversationArchived,
    conversation_title_from,
)
from src.orchestrator.logger import get_logger  # noqa: E402
from src.orchestrator.main_graph import _bridge, build_graph  # noqa: E402
from src.orchestrator.session_store import SLOTS  # noqa: E402
from src.security.audit_log import log_security_event  # noqa: E402
from src.security.gateway import IngressBlocked, enforce_ingress  # noqa: E402

logger = get_logger(__name__)

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="LankaJourney AI — Orchestration Agent",
    description=(
        "Agent 1: Supervisor node, intent router, and multi-agent coordinator "
        "for the Sri Lanka public transit planning system."
    ),
    version="1.0.0",
)

# Build and cache the graph at startup (avoids rebuilding per request)
_graph = build_graph()
logger.info("Orchestration state graph compiled and ready.")

# Accounts live beside the conversation store. In-memory when unset, so tests
# and one-off runs leave no files behind.
ACCOUNTS = AccountStore(os.environ.get("ACCOUNTS_DB_PATH") or None)

# A real hash of a value nobody knows, verified against when the email is unknown
# so that a login attempt costs the same work whether or not the account exists.
_DECOY_HASH = hash_password("decoy-password-never-issued")


# ── Request / Response schemas ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """Payload sent by the React UI or any MCP client.

    Note there is deliberately no fare field: the price is quoted by the
    Planning Agent and applied server-side, so a client cannot buy a ticket for
    less than it costs by posting a different amount.
    """

    query: str
    session_id: Optional[str] = None
    # Which conversation this turn belongs to. Omit to start one; supply it to
    # continue. A finished conversation refuses turns (see require_active).
    conversation_id: Optional[str] = None
    # R-09: the signed confirmation the traveller's approval produced, returned
    # verbatim from the previous turn. There is no boolean equivalent on purpose:
    # `hitl_approved: true` would be the client approving itself.
    hitl_token: Optional[str] = None
    # A journey with a change is confirmed leg by leg, so the client returns one
    # token per leg. `hitl_token` above stays for a single-service booking.
    hitl_tokens: list[str] = []
    selected_route_id: Optional[str] = None
    passenger_token: Optional[str] = None


class ChatResponse(BaseModel):
    """Unified response envelope returned to the frontend."""

    response: str
    conversation_id: Optional[str] = None
    session_id: str
    intent: Optional[str] = None
    route_options: list = []
    booking_reference: Optional[str] = None
    booking_status: Optional[str] = None
    # Payment step: a held seat exposes what is owed so the UI can collect it.
    transaction_id: Optional[str] = None
    # A connection holds a seat per leg, so payment covers a list.
    transaction_ids: list[str] = []
    amount_lkr: Optional[float] = None
    seat_count: Optional[int] = None
    # Populated when the planner needs the traveller to choose a mode or give a
    # time, so the UI can offer one-tap quick replies.
    clarification: Optional[dict] = None
    # R-09: the confirmation token for the booking now on screen. The UI holds it
    # while it waits for the traveller and returns it verbatim on approval.
    hitl_token: Optional[str] = None
    # A journey with a change issues one token per leg. Both come back here, and
    # both have to come back on approval.
    hitl_tokens: list[str] = []


class PaymentRequest(BaseModel):
    """
    Payment settlement request from the UI.

    `card_last4` is the only card data this system accepts; a real deployment
    would use the payment provider's hosted field so no PAN or CVV is ever
    transmitted to us.

    A journey with a change is two tickets, so `transaction_ids` carries both
    holds. `transaction_id` remains for a single-service booking.
    """

    transaction_id: str = ""
    transaction_ids: list[str] = []
    card_last4: str
    provider: str = "SLR"

    def all_transactions(self) -> list[str]:
        """Every hold to settle, de-duplicated and order-preserving."""
        seen: list[str] = []
        for candidate in [self.transaction_id, *self.transaction_ids]:
            if candidate and candidate not in seen:
                seen.append(candidate)
        return seen


class PaymentResponse(BaseModel):
    """Result of a settled payment. A connection returns one ticket per leg."""

    status: str
    booking_reference: Optional[str] = None
    receipt: dict = {}
    ticket: dict = {}
    purchase: dict = {}
    tickets: list[dict] = []
    receipts: list[dict] = []


# ── Auth ──────────────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    contact_number: Optional[str] = None
    card_number: Optional[str] = None


def current_user(
    lankajourney_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    """
    The signed-in user, or 401.

    Applied to every user-facing endpoint. The alternative — leaving /chat open —
    would mean anyone who can reach the port can read a stranger's conversations
    and book tickets as them, so this is the edge of the Zero Trust boundary, not
    a feature of the login page.
    """
    user = ACCOUNTS.resolve_session(lankajourney_session)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    return user


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        # HttpOnly so a script (including anything injected through the chat
        # renderer) cannot read the session; SameSite so it is not sent on
        # cross-site requests.
        httponly=True,
        samesite="lax",
        secure=os.environ.get("COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
        max_age=12 * 60 * 60,
        path="/",
    )


def _ensure_demo_account() -> None:
    """
    Creates one demo traveller, so the login page is not a dead end.

    The password is fixed and published on purpose: this is a demo dataset, and a
    demo account you cannot sign into does not demonstrate anything. Real
    deployments set DEMO_ACCOUNT=off and this never runs.
    """
    if os.environ.get("DEMO_ACCOUNT", "on").lower() in {"off", "0", "false", "no"}:
        return
    try:
        ACCOUNTS.create_user("demo@lankajourney.lk", "demotravel123")
        logger.info("Demo account ready (demo@lankajourney.lk / demotravel123)")
    except ValueError:
        pass  # already created


_ensure_demo_account()


@app.post("/auth/register")
def register(payload: RegisterRequest, response: Response) -> dict:
    """Creates an account and signs the traveller straight in."""
    try:
        user = ACCOUNTS.create_user(payload.email, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    token = ACCOUNTS.create_session(user["id"], payload.email)
    _set_session_cookie(response, token)
    return {"status": "OK", "user": public_profile(user)}


@app.post("/auth/login")
def login(payload: LoginRequest, response: Response) -> dict:
    """
    Signs in with an email and password.

    The same message and comparable work either way, so this cannot be used to
    discover which addresses have accounts.
    """
    user = ACCOUNTS.get_user_by_email(payload.email)
    stored = user["password_hash"] if user else _DECOY_HASH

    # Verify unconditionally: a missing user must not return faster than a wrong
    # password, or the response time enumerates accounts.
    ok = verify_password(payload.password, stored)
    if not user or not ok:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")

    token = ACCOUNTS.create_session(user["id"], payload.email)
    _set_session_cookie(response, token)
    return {"status": "OK", "user": public_profile(user)}


@app.post("/auth/logout")
def logout(
    response: Response,
    lankajourney_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    """
    Ends the session server-side.

    Clearing the cookie alone would leave a valid token in the database until it
    expired, which is exactly the state an exfiltrated token wants to be in.
    """
    if lankajourney_session:
        ACCOUNTS.delete_session(lankajourney_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "OK"}


@app.get("/auth/me")
def me(user: dict = Depends(current_user)) -> dict:
    return {"status": "OK", "user": public_profile(user)}


@app.patch("/profile")
def update_profile(payload: ProfileUpdate, user: dict = Depends(current_user)) -> dict:
    """
    Updates name, contact number and the card on file.

    The card number is used here and thrown away: it is checked, branded and
    reduced to four digits, and the full number is never stored. A client that
    tries to write its own `card_brand` or `card_last4` is not asking for a field
    that exists on this request model.
    """
    updates: dict = {}

    if payload.full_name is not None:
        name = payload.full_name.strip()
        if len(name) > 80:
            raise HTTPException(status_code=400, detail="That name is too long.")
        updates["full_name"] = name

    if payload.contact_number is not None:
        try:
            contact = normalise_contact(payload.contact_number)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        updates["contact_number"] = contact

    if payload.card_number is not None:
        try:
            brand, last4 = summarise_card(payload.card_number)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        updates["card_brand"] = brand
        updates["card_last4"] = last4
        updates["card_added_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update.")

    updated = ACCOUNTS.update_profile(user["id"], **updates)
    return {"status": "OK", "user": public_profile(updated)}


# ── Endpoints ─────────────────────────────────────────────────────────────────

GREETING = (
    "Welcome to **LankaJourney AI** 🚆\n\n"
    "Tell me where you want to go and I'll plan the journey — in **English or "
    "Singlish**. I can also check the **weather** and any **reported incidents** "
    "on your route, and book the seat for you.\n\n"
    "Try: *\"I want to go to Colombo\", *\"Heta ude Kandy indan Colombo yanna "
    "train ekak\", or *\"how will the weather be?\"*"
)


@app.post("/conversations")
async def create_conversation(user: dict = Depends(current_user)) -> dict:
    """
    Opens a conversation, greeted by the agent.

    Called when the traveller starts a new chat — including immediately after a
    payment, so the next trip starts with no memory of the last one.

    The greeting is stored as the first message rather than rendered by the UI, so
    a conversation reopened from history starts the way it actually started.
    """
    session_id = str(uuid.uuid4())
    conversation_id = CONVERSATIONS.create(session_id, greeting=GREETING)
    return {
        "status": "OK",
        "conversation_id": conversation_id,
        "session_id": session_id,
        "greeting": GREETING,
    }


@app.get("/conversations")
async def list_conversations(
    status: Optional[str] = None, user: dict = Depends(current_user)
) -> dict:
    """
    Conversation summaries for the sidebar, newest first.

    `status=active` returns the in-progress one; no filter returns everything.
    """
    return {
        "status": "OK",
        "conversations": CONVERSATIONS.list(status=status),
    }


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user: dict = Depends(current_user)) -> dict:
    """
    One conversation with its full transcript.

    Read-only by construction: there is no endpoint that appends to a finished
    conversation, because appending checks the status.
    """
    conversation = CONVERSATIONS.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    return {
        "status": "OK",
        "conversation": {
            **conversation,
            "messages": CONVERSATIONS.messages(conversation_id),
            "read_only": conversation["status"] != "active",
        },
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, user: dict = Depends(current_user)) -> ChatResponse:
    """
    Main conversational endpoint.

    The UI sends each user message here. The Orchestrator runs the state graph,
    coordinates sub-agents, and returns a structured response.
    """
    session_id = request.session_id or str(uuid.uuid4())

    # The conversation owns the session's memory. Taken from the stored record
    # rather than the request, because a client that continues a conversation
    # without resending the session id would otherwise be given a fresh one: the
    # gathered slots would land in a throwaway session and the clear-on-payment
    # would empty the wrong one.
    conversation_id = request.conversation_id
    if conversation_id:
        try:
            CONVERSATIONS.require_active(conversation_id)
        except ConversationArchived as exc:
            # 409 rather than 404: the conversation exists, it is just finished.
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        existing = CONVERSATIONS.get(conversation_id) or {}
        session_id = existing.get("session_id") or session_id
    else:
        conversation_id = CONVERSATIONS.create(session_id)

    # STEP 1 — Security Gateway Ingress. Reject adversarial input and mask PII
    # before anything reaches the intent router, the NLP parser or an LLM.
    try:
        user_query = enforce_ingress(request.query)
    except IngressBlocked as exc:
        raise HTTPException(status_code=400, detail=f"Blocked by security gateway: {exc}")

    # The NLP parser runs in the Planning Agent's module; using it here only
    # decides *which* question to ask, not the answer. The planner still owns
    # extraction and ranking.
    from src.planner.nlp_parser import extract_transit_intent

    parsed = extract_transit_intent(user_query)

    # "faster" only means a preference once we have actually asked the question.
    if not parsed.preference:
        was_asked = bool(SLOTS.get(session_id).get("preference_asked"))
        if was_asked:
            from src.planner.nlp_parser import extract_preference

            parsed = parsed.model_copy(
                update={"preference": extract_preference(user_query, asked=True)}
            )

    # Fold this turn's entities into the slots gathered so far. Route planning is
    # a slot-filling conversation: "I need to go to Colombo" then "from Kandy at
    # 8am" has to combine, or the second turn asks for the destination again.
    carried = SLOTS.merge(
        session_id,
        {
            "origin": parsed.origin,
            "destination": parsed.destination,
            "mode": "ANY" if parsed.mode == "ANY" else parsed.mode,
            "departure_date": parsed.departure_date,
            "departure_time": parsed.departure_time,
            # Seats too, or "for 3 seats" is forgotten by the next turn — and
            # the traveller is asked how many seats they want after saying it.
            #
            # Passed only when stated. The merge skips "I did not say" values, and
            # that is the rule every other slot relies on; a seat count of 1 is a
            # *default*, not an answer, so passing it unconditionally made the
            # next turn — "8am by train", which says nothing about seats —
            # quietly reset three seats back to one.
            "passengers": parsed.passengers if parsed.seat_count_stated else None,
            "seat_count_stated": True if parsed.seat_count_stated else None,
            # Only when this turn actually answered it: "" means "did not say",
            # and the merge keeps whatever the traveller chose earlier.
            "preference": parsed.preference or None,
            "preference_asked": bool(SLOTS.get(session_id).get("preference_asked")) or None,
        },
    )

    initial_state = {
        "session_id": session_id,
        "user_query": user_query,
        "intent": None,
        "extracted_entities": {
            "passenger_token": request.passenger_token or f"GUEST-{session_id[:8]}",
            # Merged slots: a value given in an earlier turn still applies.
            "origin": carried.get("origin") or "",
            "destination": carried.get("destination") or "",
            "mode": carried.get("mode") or "ANY",
            "departure_date": carried.get("departure_date") or parsed.departure_date,
            "departure_time": carried.get("departure_time"),
            # Seats and whether they were stated. Rebuilt field by field here, so
            # a new slot that is not listed is silently dropped before the graph
            # ever sees it — which is exactly what happened to these.
            "passengers": int(carried.get("passengers") or 1),
            "seat_count_stated": bool(carried.get("seat_count_stated")),
            # Whether the faster-or-cheaper question has been put to them, so a
            # reply of "faster" is only read as an answer when it was asked.
            "preference": carried.get("preference") or "",
            "preference_asked": bool(carried.get("preference_asked")),
        },
        "hitl_tokens": list(request.hitl_tokens or []),
        "route_options": [],
        "selected_route_id": request.selected_route_id,
        "booking_status": None,
        "booking_reference": None,
        "transaction_id": None,
        "transaction_ids": [],
        "amount_lkr": None,
        "seat_count": None,
        "clarification": None,
        "hitl_token": request.hitl_token,
        "messages": [],
        "next_node": "supervisor",
        "error": None,
    }

    # Name the conversation after its first meaningful question, so the history
    # list shows "Kandy to Colombo" rather than "New conversation".
    CONVERSATIONS.set_title(conversation_id, conversation_title_from(user_query))

    # Log the gateway-processed text, never the raw request: the raw string can
    # still contain an NIC or phone number, and this line goes to stdout.
    logger.info(f"[{session_id}] Query received: {user_query[:80]}…")

    try:
        result = await _graph.ainvoke(initial_state)

        # F-07: the audit log previously recorded only *blocked* input, so a
        # successful injection left no trace. Allowed traffic is now logged too —
        # metadata only (length and the classified intent), never the text.
        log_security_event(
            "QUERY_ALLOWED",
            f"intent={result.get('intent')} input_length={len(user_query)}",
        )

        messages: list = result.get("messages", [])
        response_text: str = (
            messages[-1]
            if messages
            else "I couldn't process your request. Please try again."
        )

        intent = result.get("intent")

        # Record the turn. If the traveller's payment completes, this
        # conversation is finished: it becomes read-only history and its slot
        # memory is dropped, so the next chat starts clean.
        CONVERSATIONS.append(conversation_id, "user", user_query)
        CONVERSATIONS.append(
            conversation_id, "agent", response_text, intent=intent
        )

        booking_status = result.get("booking_status")
        if booking_status == "AWAITING_PAYMENT" and result.get("transaction_id"):
            # Payment settles on /payment, so record which conversation this seat
            # belongs to now: that endpoint has no other way to know.
            CONVERSATIONS.bind_transaction(
                result["transaction_id"], conversation_id, session_id
            )

        if booking_status == "CONFIRMED":
            CONVERSATIONS.archive(
                conversation_id,
                booking_reference=result.get("booking_reference"),
            )
            SLOTS.clear(session_id)

        return ChatResponse(
            response=response_text,
            conversation_id=conversation_id,
            session_id=session_id,
            intent=intent,
            route_options=result.get("route_options", []),
            booking_reference=result.get("booking_reference"),
            booking_status=result.get("booking_status"),
            hitl_token=result.get("hitl_token"),
            transaction_id=result.get("transaction_id"),
            amount_lkr=result.get("amount_lkr"),
            seat_count=result.get("seat_count"),
        hitl_tokens=result.get("hitl_tokens") or [],
            transaction_ids=result.get("transaction_ids") or [],
            clarification=result.get("clarification"),
        )

    except Exception as exc:
        logger.error(f"[{session_id}] Graph execution error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/payment", response_model=PaymentResponse)
async def payment(request: PaymentRequest, user: dict = Depends(current_user)) -> PaymentResponse:
    """
    Settles a held seat and issues the ticket.

    The UI calls this after the human-in-the-loop gate, once the traveller has
    actually chosen how to pay. The Orchestrator stays the only client of the
    Booking Agent, so the UI never touches a sub-agent directly.

    A journey with a change arrives here as two holds and leaves as two tickets,
    which is what it is: the traveller boards two services and buys two seats.

    Only `card_last4` is accepted. A full card number must never be sent here:
    a real integration would have the payment provider's hosted field collect it
    and return an opaque token.
    """
    if not (request.card_last4.isdigit() and len(request.card_last4) == 4):
        raise HTTPException(
            status_code=400,
            detail="card_last4 must be exactly 4 digits; never send a full card number.",
        )

    transaction_ids = request.all_transactions()
    if not transaction_ids:
        raise HTTPException(status_code=400, detail="No booking to pay for.")

    settled: list[dict] = []
    for transaction_id in transaction_ids:
        try:
            response = await _bridge.settle_booking(
                transaction_id=transaction_id,
                card_last4=request.card_last4,
                provider=request.provider,
            )
        except Exception as exc:
            logger.error(f"Payment settlement failed for {transaction_id}: {exc}")
            raise HTTPException(
                status_code=502,
                detail=(
                    "Payment could not be completed."
                    + (
                        f" {len(settled)} earlier ticket(s) were issued — contact the "
                        f"operator before travelling."
                        if settled
                        else ""
                    )
                ),
            ) from exc
        settled.append(response.data or {})

    primary = settled[0]

    # The trip is done. Archive the conversation this seat was held in, and drop
    # its slot memory, so the next chat starts clean. The pairing was recorded
    # when the seat was held, so this does not trust the client to say which
    # conversation it belongs to.
    owner = None
    for transaction_id in transaction_ids:
        owner = CONVERSATIONS.transaction_owner(transaction_id)
        if owner:
            break
    if owner:
        CONVERSATIONS.archive(
            owner["conversation_id"], booking_reference=primary.get("booking_reference")
        )
        SLOTS.clear(owner["session_id"])

    return PaymentResponse(
        status="PAID",
        booking_reference=primary.get("booking_reference"),
        receipt=primary.get("receipt") or {},
        ticket=primary.get("ticket") or {},
        purchase=primary.get("purchase") or {},
        tickets=[item.get("ticket") or {} for item in settled],
        receipts=[item.get("receipt") or {} for item in settled],
    )


@app.get("/purchases")
async def purchases(user: dict = Depends(current_user)) -> dict:
    """Completed ticket purchases, newest first, for the UI purchase history."""
    try:
        response = await _bridge.fetch_purchases()
    except Exception as exc:
        logger.error(f"Could not load purchase history: {exc}")
        return {"status": "UNAVAILABLE", "purchases": []}
    return {
        "status": "OK",
        "purchases": ((response.data or {}).get("purchases") or []),
    }


class DemoIncidentRequest(BaseModel):
    """
    Demo control for the live-conditions advisory.

    Switches on an incident we invented, so the advisory path can be shown in a
    viva without waiting for a real accident. It is fed through the same
    pipeline as a live headline and is always labelled as simulated.
    """

    incident_id: Optional[str] = None
    active: bool = True


@app.get("/demo_incident")
async def demo_incident_state(user: dict = Depends(current_user)) -> dict:
    """
    Whether the incident check is currently armed.

    Read-only, for the UI to sync with on load. Without it the button starts
    showing "Simulate" every time the page is reloaded, even with an incident
    live — so the traveller presses it to clear what is already on and switches
    it on again instead.
    """
    try:
        response = await _bridge.get_incident_state()
    except Exception as exc:
        logger.warning(f"Could not read the demo incident state: {exc}")
        return {"status": "UNAVAILABLE", "active": [], "incident_check_armed": False}

    data = response.data or {}
    return {
        "status": "OK",
        "active": data.get("active") or [],
        "incident_check_armed": bool(data.get("simulated_incident_active")),
    }


@app.post("/demo_incident")
async def demo_incident(
    request: DemoIncidentRequest, user: dict = Depends(current_user)
) -> dict:
    """
    Switches a simulated incident on or off. Never affects real data.

    Authenticated because it mutates shared state: anyone who could reach it
    could put a fake accident in front of every other traveller on the demo.
    """
    try:
        response = await _bridge.set_simulated_incident(
            incident_id=request.incident_id, active=request.active
        )
    except Exception as exc:
        logger.error(f"Could not toggle the demo incident: {exc}")
        return {"status": "UNAVAILABLE", "active": [], "error": str(exc)}

    data = response.data or {}
    return {
        "status": "OK",
        "active": data.get("active") or [],
        "label": data.get("label"),
        "simulated": True,
        # Whether incident checking is armed at all, which is what the toggle
        # shows: an empty `active` list while armed means "looking, found nothing".
        #
        # Read from `simulated_incident_active`, which is the key the Conditions
        # Agent actually sends. Asking for `incident_check_armed` here returned
        # nothing, so the toggle read `false` in both directions: switching an
        # incident on never showed as on, and the button could not be pressed
        # again to switch it off.
        "incident_check_armed": bool(data.get("simulated_incident_active")),
        "message": response.message,
    }


@app.get("/pending_holds")
async def pending_holds(user: dict = Depends(current_user)) -> dict:
    """
    Bookings still awaiting payment, so the UI can offer to resume them.

    A traveller who closes the payment portal or reloads the page mid-checkout
    would otherwise lose the seat hold with no way back to it.
    """
    try:
        response = await _bridge.fetch_pending_holds()
    except Exception as exc:
        logger.error(f"Could not load pending holds: {exc}")
        return {"status": "UNAVAILABLE", "pending_holds": []}
    return {
        "status": "OK",
        "pending_holds": ((response.data or {}).get("pending_holds") or []),
    }


@app.get("/schedules")
async def schedules(
    mode: str = "ALL",
    origin: str = "",
    destination: str = "",
    limit: int = 200,
    user: dict = Depends(current_user),
) -> dict:
    """The timetable, for the schedules view. Proxied from the Planning Agent."""
    try:
        response = await _bridge.fetch_schedules(
            mode=mode, origin=origin, destination=destination, limit=limit
        )
    except Exception as exc:
        logger.error(f"Schedules unavailable: {exc}")
        raise HTTPException(status_code=503, detail="The timetable is unavailable right now.") from exc
    return response.data or {}


@app.get("/map-routes")
async def map_routes(mode: str = "ALL", user: dict = Depends(current_user)) -> dict:
    """Available routes with coordinates, for the map view."""
    try:
        response = await _bridge.fetch_map_routes(mode=mode)
    except Exception as exc:
        logger.error(f"Map routes unavailable: {exc}")
        raise HTTPException(status_code=503, detail="Route map is unavailable right now.") from exc
    return response.data or {}


@app.get("/health")
def health() -> dict:
    """Liveness probe for the orchestrator."""
    return {"status": "ok", "agent": "orchestrator", "version": "1.0.0"}

