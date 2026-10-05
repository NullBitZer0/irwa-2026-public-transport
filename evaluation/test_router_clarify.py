"""
Intent Routing Tests — CLARIFY Guard
Member 1 — Orchestration Agent

The LLM classifier labelled bare fragments like "Colombo to Galle" as CLARIFY
because they read as incomplete, so the user got "I didn't quite understand
that" instead of routes.

These tests pin the deterministic guard: when the NLP parser resolves both an
origin and a destination, the message is a route request regardless of what the
classifier said — including when the LLM call fails outright.

The Groq call is mocked, so the suite runs offline and deterministically.

Run:
    pytest evaluation/test_router_clarify.py -v
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.orchestrator import router as router_module  # noqa: E402
from src.orchestrator.router import (  # noqa: E402
    _has_journey_endpoints,
    classify_user_intent,
)


def _fake_completion(intent: str):
    """Build a stub `completion` that always returns the given intent."""

    def _call(*args, **kwargs):
        payload = {"intent": intent, "reasoning": "stubbed for tests"}
        message = SimpleNamespace(content=json.dumps(payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return _call


@pytest.fixture
def stub_llm(monkeypatch):
    """Install a stubbed LLM classifier; returns a setter for the intent."""

    def _set(intent: str):
        monkeypatch.setattr(router_module, "completion", _fake_completion(intent))

    return _set


# ── The endpoint probe ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "query, expected",
    [
        ("Colombo to Galle", True),
        ("Colombo Galle", True),
        ("Kandy indan Colombo yanawa", True),
        ("Heta ude Colombo indan Kandy yanna train ekak balanna", True),
        ("Bus from Makumbura to Matara", True),
        # Only one endpoint, or none at all — the guard leaves these alone, so a
        # single-station question still reaches clarify_node.
        ("Kandy", False),
        ("Kandy yanna train ekak thiyeda?", False),
        ("Galle yanna bus ekak thiyeda?", False),
        ("hello there", False),
        ("thanks!", False),
        ("What are the baggage rules on SLR?", False),
        ("", False),
    ],
)
def test_has_journey_endpoints(query: str, expected: bool) -> None:
    assert _has_journey_endpoints(query) is expected


# ── Guard behaviour ──────────────────────────────────────────────────────────

def test_clarify_is_upgraded_to_plan_route_for_bare_pair(stub_llm) -> None:
    """"Colombo to Galle" must plan a route even though the LLM said CLARIFY."""
    stub_llm("CLARIFY")
    assert classify_user_intent("Colombo to Galle") == "PLAN_ROUTE"


def test_singlish_bare_pair_is_upgraded(stub_llm) -> None:
    """Singlish fragments benefit from the same guard as English ones."""
    stub_llm("CLARIFY")
    assert classify_user_intent("Kandy indan Colombo yanawa") == "PLAN_ROUTE"


def test_single_endpoint_is_not_upgraded(stub_llm) -> None:
    """
    A destination-only query is left to the classifier: it usually reads as
    PLAN_ROUTE anyway, and promoting it on one station name alone would send
    "how is Kandy station?" to a route search.
    """
    stub_llm("CLARIFY")
    assert classify_user_intent("Kandy yanna bus ekak") == "CLARIFY"


def test_clarify_is_kept_when_no_endpoints(stub_llm) -> None:
    """A genuine clarification case must still reach clarify_node."""
    stub_llm("CLARIFY")
    assert classify_user_intent("hello there") == "CLARIFY"


def test_faq_is_not_overridden(stub_llm) -> None:
    """The guard must never turn a policy question into a route search."""
    stub_llm("FAQ")
    assert classify_user_intent("What are the baggage rules from Colombo to Kandy?") == "FAQ"


def test_booking_is_not_overridden(stub_llm) -> None:
    """A booking request with station names must still reach the HITL flow."""
    stub_llm("EXECUTE_BOOKING")
    assert classify_user_intent("Book route TRAIN-1001 for Colombo to Kandy") == (
        "EXECUTE_BOOKING"
    )


def test_plan_route_is_untouched(stub_llm) -> None:
    stub_llm("PLAN_ROUTE")
    assert classify_user_intent("Colombo to Galle") == "PLAN_ROUTE"


# ── LLM failure path ─────────────────────────────────────────────────────────

def test_llm_failure_falls_back_to_parser(monkeypatch) -> None:
    """With no LLM available, a route query must still be planned."""

    def _boom(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(router_module, "completion", _boom)
    assert classify_user_intent("Colombo to Galle") == "PLAN_ROUTE"


def test_llm_failure_with_no_endpoints_is_clarify(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(router_module, "completion", _boom)
    assert classify_user_intent("hello there") == "CLARIFY"


def test_invalid_intent_from_llm_is_sanitised(stub_llm) -> None:
    """An out-of-vocabulary label degrades to CLARIFY, then the guard applies."""
    stub_llm("SOMETHING_ELSE")
    assert classify_user_intent("Colombo to Galle") == "PLAN_ROUTE"
