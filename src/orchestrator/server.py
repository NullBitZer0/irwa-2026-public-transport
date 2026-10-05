"""
Orchestration Agent FastAPI Server
Member 1 — System Architect & Orchestrator Lead

Exposes the /chat endpoint consumed by the React frontend (frontend/, :3000).
The Orchestrator runs on port 8000 by default.

Start:
    uvicorn src.orchestrator.server:app --port 8000 --reload
"""

import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()  # Load .env before importing modules that read env vars

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


# ── Request / Response schemas ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """Payload sent by the React UI or any MCP client.

    Note there is deliberately no fare field: the price is quoted by the
    Planning Agent and applied server-side, so a client cannot buy a ticket for
    less than it costs by posting a different amount.
    """

    query: str
    session_id: Optional[str] = None
    hitl_approved: bool = False
    selected_route_id: Optional[str] = None
    passenger_token: Optional[str] = None


class ChatResponse(BaseModel):
    """Unified response envelope returned to the frontend."""

    response: str
    session_id: str
    intent: Optional[str] = None
    route_options: list = []
    booking_reference: Optional[str] = None
    booking_status: Optional[str] = None
    # Payment step: a held seat exposes what is owed so the UI can collect it.
    transaction_id: Optional[str] = None
    amount_lkr: Optional[float] = None
    seat_count: Optional[int] = None
    # Populated when the planner needs the traveller to choose a mode or give a
    # time, so the UI can offer one-tap quick replies.
    clarification: Optional[dict] = None


class PaymentRequest(BaseModel):
    """
    Payment settlement request from the UI.

    `card_last4` is the only card data this system accepts; a real deployment
    would use the payment provider's hosted field so no PAN or CVV is ever
    transmitted to us.
    """

    transaction_id: str
    card_last4: str
    provider: str = "SLR"


class PaymentResponse(BaseModel):
    """Result of a settled payment."""

    status: str
    booking_reference: Optional[str] = None
    receipt: dict = {}
    ticket: dict = {}
    purchase: dict = {}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """
    Main conversational endpoint.

    The UI sends each user message here. The Orchestrator runs the state graph,
    coordinates sub-agents, and returns a structured response.
    """
    session_id = request.session_id or str(uuid.uuid4())

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
        },
        "route_options": [],
        "selected_route_id": request.selected_route_id,
        "booking_status": None,
        "booking_reference": None,
        "transaction_id": None,
        "amount_lkr": None,
        "seat_count": None,
        "clarification": None,
        "hitl_approved": request.hitl_approved,
        "messages": [],
        "next_node": "supervisor",
        "error": None,
    }

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

        return ChatResponse(
            response=response_text,
            session_id=session_id,
            intent=result.get("intent"),
            route_options=result.get("route_options", []),
            booking_reference=result.get("booking_reference"),
            booking_status=result.get("booking_status"),
            transaction_id=result.get("transaction_id"),
            amount_lkr=result.get("amount_lkr"),
            seat_count=result.get("seat_count"),
            clarification=result.get("clarification"),
        )

    except Exception as exc:
        logger.error(f"[{session_id}] Graph execution error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/payment", response_model=PaymentResponse)
async def payment(request: PaymentRequest) -> PaymentResponse:
    """
    Settles a held seat and issues the ticket.

    The UI calls this after the human-in-the-loop gate, once the traveller has
    actually chosen how to pay. The Orchestrator stays the only client of the
    Booking Agent, so the UI never touches a sub-agent directly.

    Only `card_last4` is accepted. A full card number must never be sent here:
    a real integration would have the payment provider's hosted field collect it
    and return an opaque token.
    """
    if not (request.card_last4.isdigit() and len(request.card_last4) == 4):
        raise HTTPException(
            status_code=400,
            detail="card_last4 must be exactly 4 digits; never send a full card number.",
        )

    try:
        response = await _bridge.settle_booking(
            transaction_id=request.transaction_id,
            card_last4=request.card_last4,
            provider=request.provider,
        )
    except Exception as exc:
        logger.error(f"Payment settlement failed: {exc}")
        raise HTTPException(status_code=502, detail="Payment could not be completed.")

    data = response.data or {}
    return PaymentResponse(
        status="PAID",
        booking_reference=data.get("booking_reference"),
        receipt=data.get("receipt") or {},
        ticket=data.get("ticket") or {},
        purchase=data.get("purchase") or {},
    )


@app.get("/purchases")
async def purchases() -> dict:
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


@app.get("/pending_holds")
async def pending_holds() -> dict:
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


@app.get("/health")
def health() -> dict:
    """Liveness probe for the orchestrator."""
    return {"status": "ok", "agent": "orchestrator", "version": "1.0.0"}

