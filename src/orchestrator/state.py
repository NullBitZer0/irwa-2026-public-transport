"""
LangGraph Session State Schema
Member 1 — Orchestration Agent

Defines the shared TypedDict that flows through every node of the
orchestration state graph. All agents read from and write into this state.
"""

import operator
from typing import Annotated, List, Optional, TypedDict


class TransitSessionState(TypedDict):
    """
    Shared conversation state for the LangGraph orchestration graph.

    Fields are updated incrementally as the graph progresses through nodes.
    `messages` uses operator.add so each node appends rather than replaces.
    """

    # ── Session metadata ──────────────────────────────────────────────────────
    session_id: str
    user_query: str

    # ── Intent routing ────────────────────────────────────────────────────────
    intent: Optional[str]
    """Classified intent: PLAN_ROUTE | EXECUTE_BOOKING | FAQ | CLARIFY"""

    next_node: str
    """Target node decided by the supervisor for conditional edge routing."""

    # ── NLP entities (populated by Planning Agent) ────────────────────────────
    extracted_entities: Optional[dict]
    """Structured entities: origin, destination, mode, date, time, etc."""

    # ── Route planning results ────────────────────────────────────────────────
    route_options: List[dict]
    """List of RouteOption dicts returned by the Planning Agent."""

    # ── Booking state ─────────────────────────────────────────────────────────
    selected_route_id: Optional[str]
    """Route ID the user has selected for booking."""

    booking_status: Optional[str]
    """Current booking state: SEAT_HELD | CONFIRMED | EXPIRED | ERROR."""

    booking_reference: Optional[str]
    """Final booking reference (e.g. SLR-2026-XXXXXX) after confirmation."""

    transaction_id: Optional[str]
    """Seat-hold transaction id, needed by the UI payment portal."""

    amount_lkr: Optional[float]
    """Amount due for the held seat, quoted to the traveller."""

    seat_count: Optional[int]
    """Seats held, carried into the payment step."""

    clarification: Optional[dict]
    """What the planner still needs (e.g. a mode choice) plus quick replies."""

    # ── Human-in-the-Loop gate ────────────────────────────────────────────────
    hitl_approved: bool
    """True only after the user explicitly confirms the booking action in the UI."""

    # ── Conversation history ──────────────────────────────────────────────────
    messages: Annotated[List[str], operator.add]
    """Accumulated agent response messages. Each node appends its output."""

    # ── Error tracking ────────────────────────────────────────────────────────
    error: Optional[str]
    """Last error message for audit logging and user feedback."""

