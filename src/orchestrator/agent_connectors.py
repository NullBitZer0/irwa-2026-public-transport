"""
Agent Dispatch Bridge — HTTP connectors to sub-agent MCP endpoints
Member 1 — Orchestration Agent

Typed async HTTP clients connecting the Orchestrator to:
  - Planning Agent  (Member 2) at PLANNER_AGENT_URL
  - Booking Agent   (Member 3) at BOOKING_AGENT_URL
"""

import os
from typing import Optional

import httpx

from src.orchestrator.logger import get_logger
from src.orchestrator.schemas import (
    AgentResponse,
    BookingRequestPayload,
    RouteRequestPayload,
)

logger = get_logger(__name__)

_PLANNER_URL: str = os.getenv("PLANNER_AGENT_URL", "http://localhost:8001")
_BOOKING_URL: str = os.getenv("BOOKING_AGENT_URL", "http://localhost:8002")
_TIMEOUT: float = float(os.getenv("AGENT_TIMEOUT_SECONDS", "10.0"))


class AgentDispatchBridge:
    """
    Typed HTTP dispatcher connecting the Orchestrator to worker sub-agents.

    Uses JSON-RPC-style POST requests following the MCP contract defined in
    src/orchestrator/schemas.py. Both agents must expose endpoints matching
    the paths called below.
    """

    def __init__(
        self,
        planner_url: str = _PLANNER_URL,
        booking_url: str = _BOOKING_URL,
    ) -> None:
        self.planner_url = planner_url.rstrip("/")
        self.booking_url = booking_url.rstrip("/")

    # ── Planning Agent (Member 2) ─────────────────────────────────────────────

    async def call_planning_agent(self, payload: RouteRequestPayload) -> AgentResponse:
        """
        Dispatches a route-planning request to the Planning Agent.

        Endpoint: POST /mcp/plan_route
        """
        logger.info(
            f"→ Planning Agent | {payload.origin} → {payload.destination}"
            f" [{payload.travel_mode}] on {payload.date_str}"
        )
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(
                f"{self.planner_url}/mcp/plan_route",
                json=payload.model_dump(),
            )
            res.raise_for_status()
            data = res.json()
            return AgentResponse(**data)

    # ── Booking Agent (Member 3) ──────────────────────────────────────────────

    async def call_booking_agent(self, payload: BookingRequestPayload) -> AgentResponse:
        """
        Dispatches a seat-hold request to the Booking Agent.

        Endpoint: POST /mcp/hold_seat
        Note: user_confirmed must be True (HITL gate enforced by the Orchestrator).
        """
        if not payload.user_confirmed:
            raise ValueError(
                "HITL gate violation: user_confirmed must be True before dispatching "
                "to the Booking Agent."
            )
        logger.info(
            f"→ Booking Agent | route={payload.route_id}"
            f" seats={payload.seat_count} token={payload.passenger_token[:12]}…"
        )
        # Only send fields the booking agent's HoldRequest schema accepts
        hold_payload = {
            "route_id": payload.route_id,
            "provider": payload.provider,
            "passenger_token": payload.passenger_token,
            "seat_count": payload.seat_count,
            "fare_lkr": payload.fare_lkr or 0.0,
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(
                f"{self.booking_url}/mcp/hold_seat",
                json=hold_payload,
            )
            res.raise_for_status()
            data = res.json()
            return AgentResponse(**data)

    async def fetch_fare(self, route_id: str) -> Optional[float]:
        """
        Asks the Planning Agent for the authoritative fare.

        Returns None when the route has no published fare, which the caller must
        treat as "cannot sell" rather than "free". Fails closed: an unreachable
        planner returns None, never a guess.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                res = await client.get(f"{self.planner_url}/mcp/fare",
                                       params={"route_id": route_id})
                res.raise_for_status()
                payload = res.json()
        except Exception as exc:
            logger.warning(f"Fare lookup failed for {route_id}: {exc}")
            return None

        data = payload.get("data") or {}
        if data.get("fare_unknown"):
            return None
        fare = data.get("fare_lkr")
        return float(fare) if fare else None

    async def call_journey_search(
        self,
        origin: str,
        destination: str,
        travel_mode: str = "ANY",
        time_preference: Optional[str] = None,
        raw_query: str = "",
    ) -> AgentResponse:
        """
        Clarification-first journey search.

        Endpoint: POST /mcp/plan_journey
        Returns what the traveller still needs to supply (a mode, a time) and,
        when it has enough, the services that board at the requested place and
        time — including long-distance coaches that only pass through.
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(
                f"{self.planner_url}/mcp/plan_journey",
                json={
                    "origin": origin,
                    "destination": destination,
                    "travel_mode": travel_mode,
                    "time_preference": time_preference,
                    "date_str": "TODAY",
                    "raw_query": raw_query,
                },
            )
            res.raise_for_status()
            data = res.json()
            # The planner answers with the standard data envelope; expose the
            # planner-specific keys the graph needs alongside it.
            payload = dict(data.get("data") or {})
            payload["status"] = data.get("status", "SUCCESS")
            return AgentResponse(
                status=payload["status"],
                data=payload,
                message=data.get("message", ""),
            )

    async def begin_booking(self, payload: BookingRequestPayload) -> AgentResponse:
        """
        Steps 2-4: holds the seat and clears the HITL gate, then stops.

        Endpoint: POST /mcp/begin_booking
        Used by the UI payment flow: nothing is charged yet, so the client
        receives the transaction id and amount due before it collects payment.
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(
                f"{self.booking_url}/mcp/begin_booking",
                # exclude_none: optional fields the Orchestrator has no value for
                # must be omitted, not sent as null, or the Booking Agent's schema
                # rejects the payload.
                json=payload.model_dump(exclude_none=True),
            )
            res.raise_for_status()
            return AgentResponse(**res.json())

    async def settle_booking(
        self,
        transaction_id: str,
        card_last4: str,
        provider: str = "SLR",
    ) -> AgentResponse:
        """
        Steps 6-7: charges the tokenized payment and issues the ticket.

        Endpoint: POST /mcp/settle_booking
        Only the card's last four digits are sent; no PAN or CVV ever reaches an
        agent, matching the tokenized design in payment_gateway.py.
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(
                f"{self.booking_url}/mcp/settle_booking",
                json={
                    "transaction_id": transaction_id,
                    "card_last4": card_last4,
                    "provider": provider,
                },
            )
            res.raise_for_status()
            return AgentResponse(**res.json())

    async def fetch_purchases(self) -> AgentResponse:
        """
        Completed ticket purchases for the UI's purchase history.

        Endpoint: GET /mcp/purchases
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.get(f"{self.booking_url}/mcp/purchases")
            res.raise_for_status()
            return AgentResponse(**res.json())

    # ── Health checks ─────────────────────────────────────────────────────────

    async def health_check_planner(self) -> bool:
        """Returns True if the Planning Agent is reachable."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.planner_url}/health")
                return res.status_code == 200
        except Exception:
            return False

    async def health_check_booking(self) -> bool:
        """Returns True if the Booking Agent is reachable."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.booking_url}/health")
                return res.status_code == 200
        except Exception:
            return False

