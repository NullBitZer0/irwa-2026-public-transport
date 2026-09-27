"""
Prompt Injection & Adversarial Input Guardrails
Member 3 — Execution Engine & Security Lead

Blocks jailbreak attempts, pricing manipulation, and SQL/script injection
before user input reaches any LLM agent or the booking transaction layer.
Every block is written to the security audit log (see audit_log.py) so
it can be shown as evidence in the report and viva.
"""

from __future__ import annotations

import re

from src.security.audit_log import log_security_event

# Patterns that indicate adversarial or malicious prompts
_MALICIOUS_PATTERNS: list[str] = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"system\s+override",
    r"you\s+are\s+now\s+an?\s+unfiltered",
    r"act\s+as\s+(if\s+)?you\s+have\s+no\s+restrictions",
    r"set\s+fare\s+to\s+0",
    r"give\s+(me\s+)?free\s+tickets",
    r"grant\s+admin\s+access",
    r"<script[\s>]",
    r"drop\s+table",
    r"--\s*;",  # SQL comment injection
    r"base64_decode",
    r"eval\s*\(",
]

_COMPILED: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in _MALICIOUS_PATTERNS]


def sanitize_user_input(prompt: str) -> str:
    """
    Validates user input against known adversarial patterns.

    Args:
        prompt: Raw user input string.

    Returns:
        The sanitized prompt (stripped of leading/trailing whitespace).

    Raises:
        ValueError: If a malicious pattern is detected. The event is also
        recorded in the security audit log before the exception is raised.
    """
    cleaned = prompt.strip()

    for pattern in _COMPILED:
        if pattern.search(cleaned):
            log_security_event(
                "PROMPT_INJECTION_BLOCKED",
                f"pattern={pattern.pattern!r} input_length={len(cleaned)}",
            )
            raise ValueError(
                f"SECURITY_VIOLATION: Suspicious input pattern detected. "
                f"Pattern: {pattern.pattern!r}"
            )

    return cleaned


def is_safe(prompt: str) -> bool:
    """Returns True if the prompt passes all guardrail checks."""
    try:
        sanitize_user_input(prompt)
        return True
    except ValueError:
        return False
