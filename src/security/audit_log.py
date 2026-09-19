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
"""

from __future__ import annotations

import json # Used to store log information in JSON format
from datetime import datetime, timezone  # Used to record the event time
from pathlib import Path # Used to create and manage file paths

# Set the location of the security audit log file
LOG_PATH = Path(__file__).resolve().parents[2] / "evaluation" / "security_audit_log.jsonl"


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
     # Create the evaluation folder if it does not already exist
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
     # Open the log file in append mode so old logs are not deleted
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n") # Convert the event into JSON and add it as a new line


def read_recent_events(limit: int = 50) -> list[dict]:
    """Utility for a demo/report screenshot: returns the last N log entries."""
     # Return an empty list if the log file does not exist
    if not LOG_PATH.exists():
        return []
     # Open the log file and read all existing lines
    with LOG_PATH.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    return [json.loads(line) for line in lines[-limit:]] # Return only the latest events based on the given limit