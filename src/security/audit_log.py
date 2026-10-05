"""
Security Audit Logger
Member 3 — Execution Engine & Security Lead

Produces the "verification logs demonstrating how malicious prompts or
data leak attempts are blocked" deliverable named in the assignment
brief's Deliverable Ownership section.

Every blocked prompt injection and every PII redaction event is appended
as a JSON line to evaluation/security_audit_log.jsonl. This file is what
you attach to your report's Security chapter and show live in the viva.

IMPORTANT: only metadata is logged (event type, pattern matched, a short
detail string). Raw PII values are NEVER written to this log — logging
the very data you're supposed to protect would defeat the purpose.

The destination can be redirected with the SECURITY_AUDIT_LOG environment
variable. `evaluation/conftest.py` points it at a temporary file for the test
suite, so running the tests cannot mix synthetic events into the deliverable
that gets attached to the report. Live verification runs leave it unset and
therefore write to the real file.
"""

from __future__ import annotations

import json  # Used to store log information in JSON format
import os
from datetime import datetime, timezone  # Used to record the event time
from pathlib import Path  # Used to create and manage file paths

# Set the location of the security audit log file
DEFAULT_LOG_PATH = (
    Path(__file__).resolve().parents[2] / "evaluation" / "security_audit_log.jsonl"
)


def _log_path() -> Path:
    """
    Resolves the log path at call time, honouring SECURITY_AUDIT_LOG.

    Resolved lazily rather than at import so that the override applies no matter
    when it is set relative to this module being imported.
    """
    override = os.getenv("SECURITY_AUDIT_LOG")
    return Path(override) if override else DEFAULT_LOG_PATH


# Kept for callers that expect a module-level path.
LOG_PATH = DEFAULT_LOG_PATH


def log_security_event(event_type: str, detail: str) -> None:
    """
    Appends one structured event to the audit log.

    event_type: short machine-readable label, e.g.
        "PROMPT_INJECTION_BLOCKED", "PII_REDACTED", "BOOKING_CONFIRMED"
    detail: short human-readable string. Never pass raw PII here —
        pass counts / pattern names / token identifiers instead.
    """
     # Create a log entry with the current time, event type, and details
    entry = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "event_type": event_type,
        "detail": detail,
    }
    path = _log_path()
     # Create the evaluation folder if it does not already exist
    path.parent.mkdir(parents=True, exist_ok=True)
     # Open the log file in append mode so old logs are not deleted
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n") # Convert the event into JSON and add it as a new line


def read_recent_events(limit: int = 50) -> list[dict]:
    """Utility for a demo/report screenshot: returns the last N log entries."""
    path = _log_path()
     # Return an empty list if the log file does not exist
    if not path.exists():
        return []
     # Open the log file and read all existing lines
    with path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    return [json.loads(line) for line in lines[-limit:]] # Return only the latest events based on the given limit
