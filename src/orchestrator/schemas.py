"""
Inter-Agent MCP / JSON-RPC Data Contracts
Member 1 — Orchestration Agent

All typed payloads exchanged between the Orchestrator and sub-agents are
defined here. Members 2 and 3 must respect these schemas when implementing
their respective FastAPI endpoints.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ─── Orchestrator → Planning Agent ───────────────────────────────────────────

class RouteRequestPayload(BaseModel):
    """Payload dispatched from the Orchestrator to the Planning Agent."""

    origin: str = Field(description="Departure station or city name")
    destination: str = Field(description="Arrival station or city name")
    travel_mode: Literal["TRAIN", "BUS", "ANY"] = Field(
        default="ANY", description="Preferred transport mode"
    )
    date_str: str = Field(
        default="TODAY", description="Travel date: TODAY, TOMORROW, or YYYY-MM-DD"
    )
    time_preference: Optional[str] = Field(
        default=None, description="Preferred departure time, e.g. '06:00'"
    )
    passengers: int = Field(default=1, ge=1, le=10)
    raw_query: Optional[str] = Field(
        default=None, description="Original user query for NLP extraction fallback"
    )


class RouteOption(BaseModel):
    """A single transit route option returned by the Planning Agent."""

    route_id: str
    service_name: str
    provider: Literal["SLR", "SLTB", "PRIVATE_HIGHWAY"]
    origin: str
    destination: str
    departure_time: str
    arrival_time: str
    base_fare_lkr: float
    transit_type: str
    stops: List[str] = []
    classes: List[str] = []
    rrf_score: Optional[float] = None


# ─── Orchestrator → Booking Agent ────────────────────────────────────────────

class BookingRequestPayload(BaseModel):
    """Payload dispatched from the Orchestrator to the Booking Agent."""

    route_id: str
    provider: Literal["SLR", "SLTB", "PRIVATE_HIGHWAY"]
    passenger_token: str = Field(
        description="Tokenized passenger identifier (NIC token, not raw NIC)"
    )
    seat_count: int = Field(default=1, ge=1, le=6)
    fare_lkr: Optional[float] = None
    session_id: str = Field(
        default="anonymous",
        description="Conversation the HITL token is bound to (R-09)",
    )
    hitl_token: str = Field(
        default="",
        description=(
            "Signed confirmation returned by the traveller's approval. Verified "
            "server-side by the Booking Agent; there is no boolean flag, because a "
            "client-supplied one is self-asserted approval (R-09)."
        ),
    )


# ─── Generic Agent Response ───────────────────────────────────────────────────

class AgentResponse(BaseModel):
    """Standard envelope returned by all sub-agent MCP endpoints."""

    status: Literal["SUCCESS", "NEEDS_CLARIFICATION", "ERROR", "PENDING_CONFIRMATION"]
    data: Optional[Dict[str, Any]] = None
    message: str = ""

