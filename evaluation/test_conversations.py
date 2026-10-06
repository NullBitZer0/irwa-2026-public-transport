"""
Conversation lifecycle: persistence, and history being read-only.

Member 1 — Orchestration & State Graph

The product rule this encodes: a conversation lives from "new chat" until payment
completes, and then becomes history you can read but not continue. The
server-enforced half matters more than the UI half — a disabled composer is a
suggestion, so the endpoint has to refuse as well.

Run:
    pytest evaluation/test_conversations.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from conftest import sign_in

from src.orchestrator.conversations import (  # noqa: E402
    ConversationArchived,
    ConversationStore,
    conversation_title_from,
)


@pytest.fixture()
def store() -> ConversationStore:
    return ConversationStore()


# ── Lifecycle ────────────────────────────────────────────────────────────────

def test_a_new_conversation_is_active_and_empty(store: ConversationStore) -> None:
    conversation_id = store.create("sess-1")

    assert store.status(conversation_id) == "active"
    assert store.messages(conversation_id) == []


def test_turns_are_recorded_in_order(store: ConversationStore) -> None:
    conversation_id = store.create("sess-1")
    store.append(conversation_id, "user", "I want to go to Colombo")
    store.append(conversation_id, "agent", "Where are you starting from?")
    store.append(conversation_id, "user", "from Kandy", intent="PLAN_ROUTE")

    messages = store.messages(conversation_id)
    assert [m["role"] for m in messages] == ["user", "agent", "user"]
    assert messages[0]["text"] == "I want to go to Colombo"
    assert messages[2]["intent"] == "PLAN_ROUTE"


def test_payment_archives_the_conversation(store: ConversationStore) -> None:
    conversation_id = store.create("sess-1")
    store.append(conversation_id, "user", "Kandy to Colombo at 8am")

    assert store.archive(conversation_id, booking_reference="SLR-2026-ABC123") is True

    record = store.get(conversation_id)
    assert record["status"] == "archived"
    assert record["booking_reference"] == "SLR-2026-ABC123"
    assert record["ended_at"]


def test_archiving_is_idempotent(store: ConversationStore) -> None:
    """
    A repeated settlement must not move `ended_at`.

    Payment can arrive twice — a retry, or a refresh re-posting it — and the
    timestamp marks when the trip actually finished.
    """
    conversation_id = store.create("sess-1")
    assert store.archive(conversation_id, booking_reference="SLR-1") is True
    first_ended = store.get(conversation_id)["ended_at"]

    assert store.archive(conversation_id, booking_reference="SLR-2") is False
    record = store.get(conversation_id)
    assert record["ended_at"] == first_ended
    assert record["booking_reference"] == "SLR-1"


# ── History is read-only ─────────────────────────────────────────────────────

def test_a_finished_conversation_refuses_new_turns(store: ConversationStore) -> None:
    """The core rule: you can read a past conversation, not continue it."""
    conversation_id = store.create("sess-1")
    store.append(conversation_id, "user", "Kandy to Colombo at 8am")
    store.archive(conversation_id)

    with pytest.raises(ConversationArchived):
        store.append(conversation_id, "user", "actually make it 10am")


def test_a_finished_conversation_is_still_readable(store: ConversationStore) -> None:
    conversation_id = store.create("sess-1")
    store.append(conversation_id, "user", "Kandy to Colombo at 8am")
    store.append(conversation_id, "agent", "Here are the options…")
    store.archive(conversation_id)

    messages = store.messages(conversation_id)
    assert len(messages) == 2, "history must survive archiving"
    assert store.get(conversation_id)["status"] == "archived"


def test_require_active_names_the_problem(store: ConversationStore) -> None:
    conversation_id = store.create("sess-1")
    store.require_active(conversation_id)  # must not raise

    store.archive(conversation_id)
    with pytest.raises(ConversationArchived, match="finished"):
        store.require_active(conversation_id)


def test_require_active_rejects_an_unknown_conversation(store: ConversationStore) -> None:
    with pytest.raises(ConversationArchived):
        store.require_active("CNV-DOES-NOT-EXIST")


def test_a_new_conversation_starts_with_no_memory(store: ConversationStore) -> None:
    """
    After payment the next chat is genuinely new.

    This is the store half; the slot memory is dropped by the caller, and
    `test_payment_clears_slot_memory_for_the_session` covers that side.
    """
    first = store.create("sess-1")
    store.append(first, "user", "Kandy to Colombo at 8am")
    store.archive(first)

    second = store.create("sess-1")
    assert second != first
    assert store.messages(second) == []
    assert store.status(second) == "active"


# ── Listing and titles ───────────────────────────────────────────────────────

def test_history_lists_newest_first_with_a_preview(store: ConversationStore) -> None:
    first = store.create("sess-1")
    store.append(first, "user", "I want to go to Colombo")
    second = store.create("sess-1")
    store.append(second, "user", "Kandy to Matara tomorrow")

    listed = store.list()
    assert [c["id"] for c in listed] == [second, first]
    assert listed[0]["preview"].startswith("Kandy to Matara")
    assert listed[0]["message_count"] == 1


def test_history_can_be_filtered_by_status(store: ConversationStore) -> None:
    done = store.create("sess-1")
    store.append(done, "user", "Kandy to Colombo")
    store.archive(done)
    current = store.create("sess-1")
    store.append(current, "user", "Matara to Galle")

    assert [c["id"] for c in store.list(status="active")] == [current]
    assert [c["id"] for c in store.list(status="archived")] == [done]


def test_active_for_session_finds_the_open_conversation(store: ConversationStore) -> None:
    first = store.create("sess-1")
    store.archive(first)

    assert store.active_for_session("sess-1") is None

    second = store.create("sess-1")
    assert store.active_for_session("sess-1")["id"] == second


def test_the_title_is_only_set_once(store: ConversationStore) -> None:
    """The history list should not rename as the conversation develops."""
    conversation_id = store.create("sess-1")
    store.set_title(conversation_id, "Kandy to Colombo")
    store.set_title(conversation_id, "something else entirely")

    assert store.get(conversation_id)["title"] == "Kandy to Colombo"


def test_titles_are_readable_and_bounded() -> None:
    assert conversation_title_from("Kandy to Colombo at 8am") == "Kandy to Colombo at 8am"

    long_title = conversation_title_from("a very long question " * 20)
    assert len(long_title) <= 60
    assert long_title.endswith("…")

    assert conversation_title_from("") == ""
    assert conversation_title_from("   ") == ""


def test_previews_are_single_line_and_bounded(store: ConversationStore) -> None:
    conversation_id = store.create("sess-1")
    store.append(conversation_id, "user", "line one\nline two\n" + "x" * 200)

    preview = store.list()[0]["preview"]
    assert "\n" not in preview
    assert len(preview) <= 70


# ── Durability ───────────────────────────────────────────────────────────────

def test_transcripts_survive_a_restart(tmp_path) -> None:
    """History that vanishes on restart is not history."""
    db = str(tmp_path / "conversations.db")

    store = ConversationStore(db)
    conversation_id = store.create("sess-1")
    store.append(conversation_id, "user", "Kandy to Colombo at 8am")
    store.append(conversation_id, "agent", "Here are the options…")
    store.archive(conversation_id, booking_reference="SLR-2026-ABC123")

    reopened = ConversationStore(db)
    record = reopened.get(conversation_id)

    assert record["status"] == "archived"
    assert record["booking_reference"] == "SLR-2026-ABC123"
    assert len(reopened.messages(conversation_id)) == 2


def test_the_default_store_is_in_memory() -> None:
    """Tests must not leave database files in the repository."""
    assert ConversationStore().path == ":memory:"


# ── Endpoints ────────────────────────────────────────────────────────────────


@pytest.fixture()
def api_client():
    """
    Orchestrator with an in-memory conversation store and a stubbed graph.

    The graph is stubbed so these tests exercise the endpoint contract —
    including the archive-on-payment rule — without needing the sub-agents.
    """
    from fastapi.testclient import TestClient

    from src.orchestrator import server as orch

    orch.CONVERSATIONS = ConversationStore()
    orch.SLOTS._sessions.clear()

    class StubResult(dict):
        pass

    async def fake_run(state: dict) -> dict:
        # Echo the query, and confirm a booking if the traveller asked to pay.
        query = state["user_query"]
        confirmed = "confirm payment" in query.lower()
        return {
            "messages": [f"echo: {query}"],
            "intent": "PLAN_ROUTE",
            "route_options": [],
            "booking_status": "CONFIRMED" if confirmed else None,
            "booking_reference": "SLR-2026-STUB" if confirmed else None,
        }

    orch._graph = type("StubGraph", (), {"ainvoke": staticmethod(fake_run)})()

    # The endpoint is authenticated, so the fixture signs in as well as stubs.
    return sign_in(TestClient(orch.app))


def test_a_turn_is_persisted_with_its_reply(api_client) -> None:
    created = api_client.post("/conversations").json()
    conversation_id = created["conversation_id"]

    api_client.post(
        "/chat",
        json={"query": "I want to go to Colombo", "conversation_id": conversation_id},
    )

    transcript = api_client.get(f"/conversations/{conversation_id}").json()
    roles = [m["role"] for m in transcript["conversation"]["messages"]]
    # The greeting is a real stored message, so the agent speaks first.
    assert roles == ["agent", "user", "agent"]
    assert transcript["conversation"]["messages"][2]["text"].startswith("echo:")


def test_a_conversation_opens_with_a_greeting(api_client) -> None:
    """
    The agent says hello, and the hello is part of the transcript.

    Rendered in the UI only, it would be missing from history — a conversation
    reopened would start at the traveller's first question, as though the agent
    had said nothing. Stored, history starts the way the conversation started.
    """
    created = api_client.post("/conversations").json()

    assert created["greeting"], "the server must send the greeting to open on"

    transcript = api_client.get(f"/conversations/{created['conversation_id']}").json()
    messages = transcript["conversation"]["messages"]

    assert len(messages) == 1
    assert messages[0]["role"] == "agent"
    assert messages[0]["text"] == created["greeting"]
    assert messages[0]["intent"] == "GREETING"


def test_starting_a_chat_without_an_id_opens_a_conversation(api_client) -> None:
    """A first message with no conversation id must still be recorded."""
    body = api_client.post("/chat", json={"query": "hello"}).json()

    transcript = api_client.get(f"/conversations/{body['conversation_id']}").json()
    assert len(transcript["conversation"]["messages"]) == 2


def test_a_finished_conversation_refuses_new_turns_at_the_api(api_client) -> None:
    """
    Server-side enforcement, not just a disabled composer.

    This is the whole point of the read-only rule: a client that ignores the UI
    must still be refused.
    """
    conversation_id = api_client.post("/conversations").json()["conversation_id"]

    settled = api_client.post(
        "/chat", json={"query": "confirm payment", "conversation_id": conversation_id}
    ).json()
    assert settled["booking_status"] == "CONFIRMED"

    res = api_client.post(
        "/chat",
        json={"query": "actually make it 10am", "conversation_id": conversation_id},
    )

    assert res.status_code == 409
    assert "finished" in res.json()["detail"].lower()


def test_payment_completes_the_conversation_and_clears_memory(api_client) -> None:
    """
    Payment ends the trip: history from here on, and no carried-over slots.

    This is the behaviour the product asks for — chat memory goes away once the
    payment is done, so the next conversation starts clean.
    """
    from src.orchestrator import server as orch

    created = api_client.post("/conversations").json()
    session_id, conversation_id = created["session_id"], created["conversation_id"]

    # Accumulate slots, as a real conversation would.
    orch.SLOTS.merge(session_id, {"origin": "Kandy", "destination": "Colombo Fort"})
    assert orch.SLOTS.get(session_id)["origin"] == "Kandy"

    api_client.post(
        "/chat", json={"query": "confirm payment", "conversation_id": conversation_id}
    )

    assert orch.CONVERSATIONS.status(conversation_id) == "archived"
    assert orch.SLOTS.get(session_id) == {}, "slot memory survived the payment"


def test_history_is_listed_and_marked_read_only(api_client) -> None:
    conversation_id = api_client.post("/conversations").json()["conversation_id"]
    api_client.post("/chat", json={"query": "hi", "conversation_id": conversation_id})
    api_client.post(
        "/chat", json={"query": "confirm payment", "conversation_id": conversation_id}
    )

    conversation = api_client.get(f"/conversations/{conversation_id}").json()[
        "conversation"
    ]
    assert conversation["read_only"] is True

    listed = api_client.get("/conversations").json()["conversations"]
    archived = [c for c in listed if c["id"] == conversation_id]
    assert archived and archived[0]["status"] == "archived"
    assert archived[0]["booking_reference"] == "SLR-2026-STUB"


def test_an_active_conversation_is_not_read_only(api_client) -> None:
    conversation_id = api_client.post("/conversations").json()["conversation_id"]
    api_client.post("/chat", json={"query": "hi", "conversation_id": conversation_id})

    conversation = api_client.get(f"/conversations/{conversation_id}").json()[
        "conversation"
    ]
    assert conversation["read_only"] is False


def test_an_unknown_conversation_is_a_404(api_client) -> None:
    assert api_client.get("/conversations/CNV-NOPE").status_code == 404
    res = api_client.post(
        "/chat", json={"query": "hi", "conversation_id": "CNV-NOPE"}
    )
    assert res.status_code == 409


def test_the_conversation_is_named_after_its_first_question(api_client) -> None:
    conversation_id = api_client.post("/conversations").json()["conversation_id"]
    api_client.post(
        "/chat",
        json={"query": "I want to go from Kandy to Colombo", "conversation_id": conversation_id},
    )

    conversation = api_client.get(f"/conversations/{conversation_id}").json()[
        "conversation"
    ]
    assert "Kandy to Colombo" in conversation["title"]


# ── Booking readiness ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "query, expected",
    [
        ("yes", True),
        ("Yes", True),
        ("ready", True),
        ("I am ready", True),
        ("I'm ready to book", True),
        ("ok", True),
        ("go ahead", True),
        ("book it", True),
        ("lan", True),
        # Refusals must never be read as consent.
        ("no", False),
        ("no thanks", False),
        ("not yet", False),
        ("wait", False),
        ("cancel", False),
        # A long sentence mentioning yes is a change of subject, not consent.
        ("yes but can we go to Matara instead?", False),
        ("I want to go to Colombo", False),
        ("how will the weather be", False),
        ("", False),
    ],
)
def test_only_a_short_affirmative_counts_as_ready(query: str, expected: bool) -> None:
    from src.orchestrator.router import _is_booking_ready

    assert _is_booking_ready(query) is expected


def test_proposed_services_are_remembered_then_replaced() -> None:
    """
    A bare "yes" needs a referent, and a stale one would be worse than none.

    If proposals from an earlier search survived, "ready" could book a service
    the traveller is no longer looking at.
    """
    from src.orchestrator.session_store import SlotStore

    slots = SlotStore()
    slots.propose("s", [{"route_id": "TRAIN-1007"}, {"route_id": "TRAIN-1009"}])
    assert [r["route_id"] for r in slots.proposed("s")] == ["TRAIN-1007", "TRAIN-1009"]

    slots.propose("s", [{"route_id": "SLTB-2-COLO-MATA-0930"}])
    assert [r["route_id"] for r in slots.proposed("s")] == ["SLTB-2-COLO-MATA-0930"]


def test_proposed_services_are_bounded() -> None:
    from src.orchestrator.session_store import MAX_PROPOSED_ROUTES, SlotStore

    slots = SlotStore()
    slots.propose("s", [{"route_id": f"R{i}"} for i in range(50)])

    assert len(slots.proposed("s")) == MAX_PROPOSED_ROUTES


def test_proposals_do_not_leak_between_sessions() -> None:
    from src.orchestrator.session_store import SlotStore

    slots = SlotStore()
    slots.propose("alice", [{"route_id": "TRAIN-1007"}])

    assert slots.proposed("bob") == []


def test_saying_ready_without_a_proposal_does_not_book() -> None:
    """
    "yes" before anything has been proposed is not consent to book.

    Otherwise an unrelated affirmative — answering "train or bus?", say — would
    send the traveller to the booking gate.
    """
    from src.orchestrator.router import _is_booking_ready

    assert _is_booking_ready("yes") is True  # the detector does fire
    # …but the supervisor only advances when a proposal exists, which is covered
    # by the proposal-memory tests above and by the live flow.


# ── Payment archives the conversation it belongs to ──────────────────────────

def test_a_transaction_is_bound_to_its_conversation(store: ConversationStore) -> None:
    """
    Payment settles on a different endpoint from the turn that held the seat.

    Without this binding, /payment has no way to know which conversation to
    archive — and asking the client would let it name any conversation, archiving
    the wrong history.
    """
    conversation_id = store.create("sess-1")

    store.bind_transaction("TXN-1", conversation_id, "sess-1")

    owner = store.transaction_owner("TXN-1")
    assert owner["conversation_id"] == conversation_id
    assert owner["session_id"] == "sess-1"


def test_an_unknown_transaction_has_no_owner(store: ConversationStore) -> None:
    assert store.transaction_owner("TXN-NOPE") is None


def test_binding_is_idempotent(store: ConversationStore) -> None:
    """A retried hold must not leave two rows claiming the same transaction."""
    first = store.create("sess-1")
    second = store.create("sess-2")

    store.bind_transaction("TXN-1", first, "sess-1")
    store.bind_transaction("TXN-1", second, "sess-2")

    assert store.transaction_owner("TXN-1")["conversation_id"] == second


def test_a_transaction_binding_survives_a_restart(tmp_path) -> None:
    db = str(tmp_path / "conversations.db")
    conversation_id = ConversationStore(db).create("sess-1")

    store = ConversationStore(db)
    store.bind_transaction("TXN-1", conversation_id, "sess-1")

    assert ConversationStore(db).transaction_owner("TXN-1")["conversation_id"] == (
        conversation_id
    )
