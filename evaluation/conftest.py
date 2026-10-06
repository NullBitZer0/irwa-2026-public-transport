"""
Shared pytest configuration for the evaluation suite.

The security audit log (`evaluation/security_audit_log.jsonl`) is a report
deliverable: it is the evidence attached to the Security chapter and shown in the
viva. The security tests deliberately trigger redactions and blocked injections,
so without redirection every test run would append synthetic events to that file
and make the evidence indistinguishable from a real verification run.

Pointing SECURITY_AUDIT_LOG at a temporary file keeps the deliverable clean.
Live verification runs leave the variable unset and write to the real file.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.orchestrator.accounts import AccountStore  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def isolate_security_audit_log(tmp_path_factory):
    """Redirect audit logging away from the report deliverable for the whole run."""
    target = tmp_path_factory.mktemp("audit") / "security_audit_log.jsonl"
    previous = os.environ.get("SECURITY_AUDIT_LOG")

    os.environ["SECURITY_AUDIT_LOG"] = str(target)
    try:
        yield target
    finally:
        if previous is None:
            os.environ.pop("SECURITY_AUDIT_LOG", None)
        else:
            os.environ["SECURITY_AUDIT_LOG"] = previous


@pytest.fixture(autouse=True)
def _hitl_signing_key(monkeypatch):
    """
    A per-test confirmation signing key (R-09).

    Autouse because the booking endpoints fail closed without HITL_TOKEN_SECRET:
    without this, every booking test would 503 for a reason unrelated to what it
    is testing. Tests that care about the missing-key behaviour set the variable
    themselves via monkeypatch.
    """
    monkeypatch.setenv("HITL_TOKEN_SECRET", "test-only-hitl-signing-key-0123456789")


def sign_in(client) -> object:
    """
    Registers a throwaway traveller on a TestClient and returns it signed in.

    The user-facing API is authenticated, so any test that calls /chat or
    /conversations needs a session. Doing it here keeps that one line instead of
    a bespoke login in each test, and means a test that forgets is visibly
    missing a call rather than mysteriously asserting against a 401.
    """
    import uuid

    client.post(
        "/auth/register",
        json={
            "email": f"test-{uuid.uuid4().hex[:10]}@lankajourney.lk",
            "password": "test-only-password",
        },
    )
    return client


@pytest.fixture()
def authed_client():
    """A signed-in TestClient for the orchestrator API."""
    from fastapi.testclient import TestClient

    from src.orchestrator import server as orch

    orch.ACCOUNTS = AccountStore()
    return sign_in(TestClient(orch.app))
