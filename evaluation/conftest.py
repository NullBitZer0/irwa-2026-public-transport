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

import pytest


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
