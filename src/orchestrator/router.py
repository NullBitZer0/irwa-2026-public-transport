"""
Intent Classification & Supervisor Router
Member 1 — Orchestration Agent

Classifies free-form user input into one of four intents using Groq LLM
via LiteLLM at temperature=0 for deterministic routing.
"""

import json
import os
import re
from typing import Literal, Optional

from litellm import completion

from src.orchestrator.logger import get_logger

logger = get_logger(__name__)

ROUTER_MODEL: str = os.getenv("GROQ_MODEL", "groq/llama-3.1-8b-instant")

Intent = Literal["PLAN_ROUTE", "EXECUTE_BOOKING", "CONDITIONS", "FAQ", "CLARIFY"]
VALID_INTENTS: tuple[str, ...] = (
    "PLAN_ROUTE",
    "EXECUTE_BOOKING",
    "CONDITIONS",
    "FAQ",
    "CLARIFY",
)

_SYSTEM_PROMPT = """\
You are the intent classifier for LankaJourney AI, Sri Lanka's public transit assistant.
Classify the user's message into EXACTLY ONE of these intents:

  PLAN_ROUTE      – User wants to find trains, buses, schedules, or routes.
  EXECUTE_BOOKING – User explicitly wants to reserve or book a seat/ticket.
  FAQ             – Questions about baggage, refunds, station facilities, or policies.
  CONDITIONS      – Questions about the weather, road/rail incidents, strikes,
                    disruptions, or whether it is safe to travel right now.
  CLARIFY         – Input is too vague, incomplete, or off-topic.

Notes:
- Users may write in English or Singlish (colloquial Sinhala in English letters).
- Singlish glossary: "heta" = TOMORROW, "ada" = TODAY, "indan"/"idala"/"sita" = from,
  "yanna"/"yanawa" = to, "ekak" = a/an, "thiyeda" = is there, "balanna" = check/look,
  "bas" = bus, "dumriya"/"relya" = train.
- If the user mentions a route ID AND says "book" / "reserve" (or "book karanna") → EXECUTE_BOOKING.
- If the user only asks about schedules or fares → PLAN_ROUTE.
- Questions about baggage/refund/policy (or "kohomada refund karanne") → FAQ.
- Questions about weather, rain, storms, incidents, strikes, protests, closures or
  "is it safe to travel" → CONDITIONS. This is checked BEFORE PLAN_ROUTE, because
  "how will the weather be on the way to Kandy" is a conditions question even
  though it names a destination.
- A bare pair of places is still a route request: "Colombo to Galle",
  "Colombo Galle", "Kandy yanna" → PLAN_ROUTE. Only use CLARIFY when no
  journey can be identified at all (greetings, thanks, unrelated small talk).

Respond ONLY with valid JSON (no markdown, no extra text):
{"intent": "<INTENT>", "reasoning": "<one short sentence>"}
"""


def _has_journey_endpoints(query: str) -> bool:
    """
    True when the deterministic parser can resolve a usable journey request.

    The LLM classifier tends to label bare fragments such as "Colombo to Galle"
    as CLARIFY because they look incomplete. Entity extraction is deterministic
    and language-agnostic (English and Singlish), so a resolved endpoint is
    stronger evidence of a route request than the LLM's verdict.

    Two endpoints ("Kandy to Colombo") speak for themselves. With only one, the
    message is a partial request — "I need to go to Colombo" — which is still a
    route request, just one whose origin is not known yet, so it is routed to
    the planner to be asked for rather than rejected as unintelligible. That
    only counts when the message also reads as travel; "Is Kandy Fort station
    accessible?" mentions a station but wants to know about the station.
    """
    try:
        # Imported lazily: the parser belongs to the Planning Agent's module and
        # is only needed on the fallback path.
        from src.planner.nlp_parser import extract_transit_intent

        parsed = extract_transit_intent(query)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Endpoint probe failed ({type(exc).__name__}): {exc}")
        return False

    if parsed.origin and parsed.destination:
        # "Kandy Fort" resolves to both endpoints, so a question *about that
        # station* parses as a complete journey. The guard this function's own
        # docstring describes could never fire while the check was "two
        # endpoints and stop".
        return not _asks_about_a_place(query)

    if parsed.origin or parsed.destination:
        return _looks_like_travel_request(query, parsed)

    # No place name at all, but unmistakably about travelling — "train up into
    # the tea country". A journey whose endpoints were given as a region rather
    # than a station. It belongs to the planner, which asks for what is missing;
    # sending it to the clarify node tells the traveller we did not understand a
    # sentence that plainly asked for a train.
    return _looks_like_unplaced_travel_request(parsed)


def _session_is_mid_question(slots: Optional[dict]) -> bool:
    """
    True when the conversation has one endpoint and is waiting on the other.

    The origin alone does not make a journey: "I need to go to Colombo" has a
    destination and nowhere to start from, which is a question, not a search.
    Judged on its own a one-word reply like "Ella" looks like nonsense, but it is
    the missing half of a trip the traveller is already building.
    """
    if not slots:
        return False
    return bool(slots.get("origin") or slots.get("destination"))


def _session_has_journey(slots: Optional[dict]) -> bool:
    """
    True when the conversation so far is already a journey in progress.

    Answering a clarification often means naming only what was asked for —
    "at 8am by train" carries no place name at all. Judged on this message
    alone that looks like nonsense, but it is the missing slot for a trip whose
    origin and destination were given a turn earlier, so it must continue the
    route search rather than restart the conversation.
    """
    if not slots:
        return False
    return bool(slots.get("origin") and slots.get("destination"))


# Words that signal the traveller is trying to go somewhere, as opposed to
# asking about a place that happens to be named.
_TRAVEL_INTENT_WORDS: frozenset[str] = frozenset(
    {
        "go", "going", "goto", "travel", "travelling", "traveling", "trip",
        "journey", "catch", "board", "take", "ride", "need", "want", "wanna",
        "plan", "reach", "get", "leave", "depart", "from", "to",
        # Singlish
        "yanna", "yan", "yanawa", "yanne", "enna", "enawa", "inbu", "inne",
        "yana", "yane", "ganna", "thawa", "hithala", "kanda",
    }
)


# Weather and disruption vocabulary, so these questions are recognised without a
# model. Needed because the router falls back to deterministic classification when
# no LLM key is configured, and "how will the weather be?" has no journey
# endpoints to detect — it would otherwise be filed as off-topic.
CONDITION_TERMS: frozenset[str] = frozenset(
    {
        "weather", "rain", "raining", "storm", "flood", "wind", "windy", "heat",
        "forecast", "climate", "sunny", "cloudy", "thunder",
        "incident", "incidents", "disruption", "disruptions", "strike", "protest",
        "cancelled", "canceled", "blocked", "closure", "closed", "delayed",
        "delay", "delay",
        # Singlish: wassa/bera = rain, ghataya = incident, stike = strike,
        # "hawa" is used for both weather and rain.
        "hawa", "wassa", "bera", "ghataya", "stike", "prudesh",
    }
)

# Phrases that signal the traveller is asking whether it is safe to go, which is
# the question behind most incident enquiries.
_SAFETY_TERMS = frozenset({"safe", "sarf", "worth", "yanna", "travel"})


# Answers to "are you ready to book?", once the agent has asked it.
READY_WORDS: frozenset[str] = frozenset(
    {
        "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "ready", "go",
        "proceed", "book", "bookit", "confirm", "done", "alright", "fine",
        "agreed", "lan", "hodama",
    }
)

# Phrases that must NOT be read as consent. "no thanks" contains "thanks", and
# treating a refusal as approval would book a seat the traveller declined — the
# worst possible failure of this feature.
NEGATIVE_WORDS: frozenset[str] = frozenset(
    {"no", "nope", "nah", "not", "never", "dont", "stop", "wait", "cancel", "nathava"}
)


def _is_booking_ready(query: str) -> bool:
    """
    Did the traveller just say yes to booking?

    Only a short affirmative counts. A bare "yes" is consent; a long sentence
    containing "yes" is somebody changing the subject, and treating it as consent
    would hold a seat they did not agree to.
    """
    tokens = re.findall(r"[a-z']+", query.lower())
    if not tokens or len(tokens) > 4:
        return False
    if any(t in NEGATIVE_WORDS for t in tokens):
        return False
    return any(t in READY_WORDS for t in tokens)


def _looks_like_conditions_query(query: str) -> bool:
    """
    Does this message ask about weather or a disruption?

    Checked before journey detection because "how will the weather be on the way
    to Kandy" names a destination, and without this it would be routed into a
    route search that never answers the question asked.
    """
    lowered = query.lower()
    has_condition = any(re.search(rf"\b{re.escape(t)}\w*\b", lowered) for t in CONDITION_TERMS)

    # "is it safe to travel" with no other condition word is still a conditions
    # question — it is how travellers usually ask about strikes.
    if not has_condition:
        has_condition = "safe" in lowered and any(
            word in lowered for word in ("travel", "go", "yanna", "sarf")
        )
    return has_condition


# Asking *about* a place, rather than asking to travel to it. A station name plus
# one of these is a question about the station — it wants directions,
# accessibility or facilities, not a timetable, and answering it with route
# options answers a different question.
_PLACE_QUESTION_WORDS: frozenset[str] = frozenset(
    {
        "accessible", "accessibility", "wheelchair", "parking", "toilet",
        "restroom", "location", "located", "address", "map", "facilities",
        "facility", "platform", "counter", "shelter", "sandbox",
    }
)


def _asks_about_a_place(query: str) -> bool:
    """True when the message asks about a station rather than a journey to it."""
    tokens = set(re.findall(r"[a-z]+", query.lower()))
    return bool(tokens & _PLACE_QUESTION_WORDS)


def _looks_like_travel_request(query: str, parsed: object) -> bool:
    """
    Does a single-endpoint message read as a request to travel somewhere?

    A time or an explicit mode is itself travel intent, so those count without
    needing a verb.
    """
    if getattr(parsed, "departure_time", None) or getattr(parsed, "mode", "ANY") != "ANY":
        return True

    tokens = re.findall(r"[a-z]+", query.lower())
    return any(token in _TRAVEL_INTENT_WORDS for token in tokens)


def _looks_like_unplaced_travel_request(parsed: object) -> bool:
    """
    Does a message with no resolvable place still ask for a journey?

    Stricter than `_looks_like_travel_request`, which is safe whenever a station
    was named. With nothing named, "want" and "get" are far too common in
    sentences that are not route searches — "I want to know about baggage"
    would be dragged into a journey search and answered with a question about
    origins. So this requires an explicit transport mode or a departure time:
    the traveller naming the thing they want without saying where.

    The planner then asks for the endpoints it is missing, which is the same
    question a traveller who typed half a sentence already gets.
    """
    return (
        getattr(parsed, "mode", "ANY") != "ANY"
        or bool(getattr(parsed, "departure_time", None))
    )


def classify_user_intent(
    query: str,
    has_selected_route: bool = False,
    session_slots: Optional[dict] = None,
) -> Intent:
    """
    Calls Groq via LiteLLM to classify the user's intent.

    Args:
        query: The raw user message (English / Singlish / Sinhala).
        has_selected_route: Whether the session already has a route selected.
        session_slots: Slots gathered in earlier turns of this conversation.

    Returns:
        One of: "PLAN_ROUTE", "EXECUTE_BOOKING", "CONDITIONS", "FAQ", "CLARIFY".
        Falls back to "PLAN_ROUTE" (when endpoints are detectable) else "CLARIFY".
    """
    user_content = (
        f"User message: {query}\n"
        f"Has a route already been selected in this session: {has_selected_route}"
    )

    # A journey already in progress makes this turn a continuation of it, so
    # telling the classifier about it helps rather than only helping afterwards.
    if _session_has_journey(session_slots):
        user_content += (
            "\nThe traveller is already planning a journey in this conversation"
            f" (from {session_slots.get('origin')} to {session_slots.get('destination')})."
            " A short reply naming only a time or a mode is them answering a"
            " question about that journey."
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

        # A weather or incident question is not a route request, whatever the
        # classifier decided.
        if intent in ("CLARIFY", "PLAN_ROUTE") and _looks_like_conditions_query(query):
            logger.info("Intent → CONDITIONS (weather/disruption vocabulary)")
            return "CONDITIONS"

        # Guard: a resolvable journey means this is a route request, whatever the
        # classifier decided. Either this message names the endpoints, or the
        # conversation already has them and this turn is answering a question.
        if intent == "CLARIFY" and (
            _has_journey_endpoints(query)
            or _session_has_journey(session_slots)
            or _session_is_mid_question(session_slots)
        ):
            if _session_has_journey(session_slots):
                reason = "journey already in progress"
            elif _session_is_mid_question(session_slots):
                reason = "waiting on the other endpoint"
            else:
                reason = "origin and destination detected"
            logger.info(f"Intent CLARIFY → PLAN_ROUTE ({reason})")
            return "PLAN_ROUTE"

        return intent  # type: ignore[return-value]

    except Exception as exc:
        logger.error(f"Intent classification failed ({type(exc).__name__}): {exc}")
        # No classifier available — decide deterministically, in the same order of
        # precedence the prompt asks for.
        if _looks_like_conditions_query(query):
            return "CONDITIONS"
        return (
            "PLAN_ROUTE"
            if (
                _has_journey_endpoints(query)
                or _session_has_journey(session_slots)
                or _session_is_mid_question(session_slots)
            )
            else "CLARIFY"
        )

