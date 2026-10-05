"""
Security Gateway Ingress
Member 3 — Execution Engine & Security Lead (shared ingress)

Implements STEP 1 of the mandated security pipeline:

    Incoming Query
        │
        ▼
    ┌───────────────────────────────────────────┐
    │  STEP 1: Security Gateway Ingress         │
    │   • Regex prompt-injection check           │
    │   • PII masking: NIC / phone → tokens      │
    └──────────────────┬────────────────────────┘
                       ▼
            Booking Agent dispatch

Every free-form message must pass through `enforce_ingress()` BEFORE it reaches
any LLM. Previously the checks existed but were only reachable at the Booking
Agent's `hold_seat`, so a query submitted to the Orchestrator's `/chat` endpoint
reached the intent router and the NLP parser unfiltered — and PII masking was
never invoked on a live request path at all.

Order matters and follows the brief: reject adversarial input first, then redact
the identifiers out of what remains.
"""

from __future__ import annotations

from src.security.guardrails import sanitize_user_input
from src.security.pii_masker import PIITokenizer

# Process-scoped vault. Tokens are opaque and the real values stay AES-256-GCM
# encrypted in memory, so nothing sensitive is logged or sent to an LLM.
_tokenizer = PIITokenizer()


class IngressBlocked(ValueError):
    """Raised when input is rejected by the gateway (adversarial patterns)."""


def enforce_ingress(text: str) -> str:
    """
    Applies the full gateway to one user message.

    Args:
        text: raw user input, in any supported language.

    Returns:
        The text that is safe to hand to an agent: adversarial patterns removed by
        rejection, and PII replaced with opaque tokens.

    Raises:
        IngressBlocked: if the input matches a known adversarial pattern.
            The event has already been written to the security audit log.
    """
    try:
        sanitize_user_input(text)
    except ValueError as exc:
        # sanitize_user_input logs PROMPT_INJECTION_BLOCKED before raising.
        raise IngressBlocked(str(exc)) from exc

    return _tokenizer.redact(text)


def redact(text: str) -> str:
    """PII masking only, for payloads that are not free-form prompts."""
    return _tokenizer.redact(text)


def detokenize(value: str) -> str:
    """
    Resolves a token back to its real value.

    Only valid inside the process that issued it (the Booking Agent's mock
    gateway). Orchestrator-issued tokens live in a different vault, so this is a
    safe no-op for them — see the module note in the report.
    """
    return _tokenizer.detokenize(value)


def vault_size() -> int:
    """Number of tokens currently held. Tokens themselves are never logged."""
    return len(_tokenizer.vault)
