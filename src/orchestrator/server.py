"""
Orchestration Agent FastAPI Server
Member 1 — System Architect & Orchestrator Lead

Exposes the /chat endpoint consumed by the Streamlit UI (src/app.py).
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
from src.orchestrator.main_graph import build_graph  # noqa: E402

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
    """Payload sent by the Streamlit UI or any MCP client."""

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


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """
    Main conversational endpoint.

    The UI sends each user message here. The Orchestrator runs the state graph,
    coordinates sub-agents, and returns a structured response.
    """
    session_id = request.session_id or str(uuid.uuid4())

    initial_state = {
        "session_id": session_id,
        "user_query": request.query,
        "intent": None,
        "extracted_entities": {
            "passenger_token": request.passenger_token or f"GUEST-{session_id[:8]}"
        },
        "route_options": [],
        "selected_route_id": request.selected_route_id,
        "booking_status": None,
        "booking_reference": None,
        "hitl_approved": request.hitl_approved,
        "messages": [],
        "next_node": "supervisor",
        "error": None,
    }

    try:
        logger.info(f"[{session_id}] Query received: {request.query[:80]}…")
        result = await _graph.ainvoke(initial_state)

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
        )

    except Exception as exc:
        logger.error(f"[{session_id}] Graph execution error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health")
def health() -> dict:
    """Liveness probe for the orchestrator."""
    return {"status": "ok", "agent": "orchestrator", "version": "1.0.0"}

