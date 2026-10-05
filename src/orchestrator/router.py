"""
Intent Classification & Supervisor Router
Member 1 — Orchestration Agent

Classifies free-form user input into one of four intents using Groq LLM
via LiteLLM at temperature=0 for deterministic routing.
"""

import json
import os
from typing import Literal

from litellm import completion

from src.orchestrator.logger import get_logger

logger = get_logger(__name__)

ROUTER_MODEL: str = os.getenv("GROQ_MODEL", "groq/llama-3.1-8b-instant")

Intent = Literal["PLAN_ROUTE", "EXECUTE_BOOKING", "FAQ", "CLARIFY"]
VALID_INTENTS: tuple[str, ...] = ("PLAN_ROUTE", "EXECUTE_BOOKING", "FAQ", "CLARIFY")

_SYSTEM_PROMPT = """\
You are the intent classifier for LankaJourney AI, Sri Lanka's public transit assistant.
Classify the user's message into EXACTLY ONE of these intents:

  PLAN_ROUTE      – User wants to find trains, buses, schedules, or routes.
  EXECUTE_BOOKING – User explicitly wants to reserve or book a seat/ticket.
  FAQ             – Questions about baggage, refunds, station facilities, or policies.
  CLARIFY         – Input is too vague, incomplete, or off-topic.

Notes:
- Users may write in English or Singlish (colloquial Sinhala in English letters).
- Singlish glossary: "heta" = TOMORROW, "ada" = TODAY, "indan"/"idala"/"sita" = from,
  "yanna"/"yanawa" = to, "ekak" = a/an, "thiyeda" = is there, "balanna" = check/look,
  "bas" = bus, "dumriya"/"relya" = train.
- If the user mentions a route ID AND says "book" / "reserve" (or "book karanna") → EXECUTE_BOOKING.
- If the user only asks about schedules or fares → PLAN_ROUTE.
- Questions about baggage/refund/policy (or "kohomada refund karanne") → FAQ.
- A bare pair of places is still a route request: "Colombo to Galle",
  "Colombo Galle", "Kandy yanna" → PLAN_ROUTE. Only use CLARIFY when no
  journey can be identified at all (greetings, thanks, unrelated small talk).

Respond ONLY with valid JSON (no markdown, no extra text):
{"intent": "<INTENT>", "reasoning": "<one short sentence>"}
"""


def _has_journey_endpoints(query: str) -> bool:
    """
    True when the deterministic parser can resolve both an origin and a destination.

    The LLM classifier tends to label bare fragments such as "Colombo to Galle"
    as CLARIFY because they look incomplete. Entity extraction is deterministic
    and language-agnostic (English and Singlish), so a resolved origin +
    destination is stronger evidence of a route request than the LLM's verdict.
    """
    try:
        # Imported lazily: the parser belongs to the Planning Agent's module and
        # is only needed on the fallback path.
        from src.planner.nlp_parser import extract_transit_intent

        parsed = extract_transit_intent(query)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Endpoint probe failed ({type(exc).__name__}): {exc}")
        return False

    return bool(parsed.origin and parsed.destination)


def classify_user_intent(
    query: str,
    has_selected_route: bool = False,
) -> Intent:
    """
    Calls Groq via LiteLLM to classify the user's intent.

    Args:
        query: The raw user message (English / Singlish / Sinhala).
        has_selected_route: Whether the session already has a route selected.

    Returns:
        One of: "PLAN_ROUTE", "EXECUTE_BOOKING", "FAQ", "CLARIFY".
        Falls back to "PLAN_ROUTE" (when endpoints are detectable) else "CLARIFY".
    """
    user_content = (
        f"User message: {query}\n"
        f"Has a route already been selected in this session: {has_selected_route}"
    )

    try:
        response = completion(
            model=ROUTER_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=120,
        )
        raw: str = response.choices[0].message.content or ""
        data: dict = json.loads(raw)

        intent: str = data.get("intent", "CLARIFY").strip().upper()
        reasoning: str = data.get("reasoning", "")

        if intent not in VALID_INTENTS:
            logger.warning(f"Unknown intent '{intent}' returned — defaulting to CLARIFY")
            intent = "CLARIFY"

        logger.info(f"Intent → {intent} | Reason: {reasoning} | Model: {ROUTER_MODEL}")

        # Guard: a resolvable origin + destination means this is a route request,
        # whatever the classifier decided.
        if intent == "CLARIFY" and _has_journey_endpoints(query):
            logger.info("Intent CLARIFY → PLAN_ROUTE (origin and destination detected)")
            return "PLAN_ROUTE"

        return intent  # type: ignore[return-value]

    except Exception as exc:
        logger.error(f"Intent classification failed ({type(exc).__name__}): {exc}")
        # The LLM is unavailable — fall back to deterministic entity extraction
        # so a plain route query still works.
        return "PLAN_ROUTE" if _has_journey_endpoints(query) else "CLARIFY"

