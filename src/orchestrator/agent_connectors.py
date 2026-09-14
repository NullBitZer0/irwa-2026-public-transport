"""
Agent Dispatch Bridge — HTTP connectors to sub-agent MCP endpoints
Member 1 — Orchestration Agent

Typed async HTTP clients connecting the Orchestrator to:
  - Planning Agent  (Member 2) at PLANNER_AGENT_URL
  - Booking Agent   (Member 3) at BOOKING_AGENT_URL
"""

import os

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
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(
                f"{self.booking_url}/mcp/hold_seat",
                json=payload.model_dump(),
            )
            res.raise_for_status()
            data = res.json()
            return AgentResponse(**data)

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

